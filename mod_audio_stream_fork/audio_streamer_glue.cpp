#include <string>
#include <cstring>
#include "mod_audio_stream.h"
#include "WebSocketClient.h"
#include <switch_json.h>
#include <fstream>
#include <switch_buffer.h>
#include <unordered_map>
#include <unordered_set>
#include <atomic>
#include <vector>
#include "base64.h"

#define FRAME_SIZE_8000  320 /* 1000x0.02 (20ms)= 160 x(16bit= 2 bytes) 320 frame size*/

static switch_log_level_t get_stream_log_level(switch_core_session_t *session, switch_log_level_t default_level) {
    switch_channel_t *channel = switch_core_session_get_channel(session);
    const char *level = switch_channel_get_variable(channel, "STREAM_LOG_LEVEL");
    if (!level || !*level) return default_level;
    if (!strcasecmp(level, "ERROR")) return SWITCH_LOG_ERROR;
    if (!strcasecmp(level, "WARNING")) return SWITCH_LOG_WARNING;
    if (!strcasecmp(level, "INFO")) return SWITCH_LOG_INFO;
    if (!strcasecmp(level, "DEBUG")) return SWITCH_LOG_DEBUG;
    return default_level;
}

static inline uint8_t linear_to_ulaw(int16_t pcm_val) {
    static const int16_t BIAS = 0x84;
    static const int16_t CLIP = 32635;
    static const uint8_t exp_lut[256] = {
        0,0,1,1,2,2,2,2,3,3,3,3,3,3,3,3,
        4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
        7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7
    };
    int sign = (pcm_val >> 8) & 0x80;
    if (sign) pcm_val = -pcm_val;
    if (pcm_val > CLIP) pcm_val = CLIP;
    pcm_val = pcm_val + BIAS;
    int exponent = exp_lut[(pcm_val >> 7) & 0xFF];
    int mantissa = (pcm_val >> (exponent + 3)) & 0x0F;
    return (uint8_t)~(sign | (exponent << 4) | mantissa);
}

class AudioStreamer {
public:

    AudioStreamer(const char* uuid, const char* wsUri, responseHandler_t callback, int deflate, int heart_beat,
                    bool suppressLog, const char* extra_headers, bool no_reconnect,
                    const char* tls_cafile, const char* tls_keyfile, const char* tls_certfile,
                    bool tls_disable_hostname_validation): m_sessionId(uuid), m_notify(callback),
                    m_suppress_log(suppressLog), m_extra_headers(extra_headers), m_playFile(0){

        WebSocketHeaders hdrs;
        WebSocketTLSOptions tls;

        if (m_extra_headers) {
            cJSON *headers_json = cJSON_Parse(m_extra_headers);
            if (headers_json) {
                cJSON *iterator = headers_json->child;
                while (iterator) {
                    if (iterator->type == cJSON_String && iterator->valuestring != nullptr) {
                        hdrs.set(iterator->string, iterator->valuestring);
                    }
                    iterator = iterator->next;
                }
                cJSON_Delete(headers_json);
            }
        }

        client.setUrl(wsUri);

        // Setup eventual TLS options.
        // tls_cafile may hold the special values
        // NONE, which disables validation and SYSTEM which uses
        // the system CAs bundle
        if (tls_cafile) {
            tls.caFile = tls_cafile;
        }

        if (tls_keyfile) {
            tls.keyFile = tls_keyfile;
        }

        if (tls_certfile) {
            tls.certFile = tls_certfile;
        }

        tls.disableHostnameValidation = tls_disable_hostname_validation;
        client.setTLSOptions(tls);

        // Optional heart beat, sent every xx seconds when there is not any traffic
        // to make sure that load balancers do not kill an idle connection.
        if(heart_beat)
            client.setPingInterval(heart_beat);

        // Per message deflate connection is enabled by default. You can tweak its parameters or disable it
        if(deflate)
            client.enableCompression(false);

        // Set extra headers if any
        if(!hdrs.empty())
            client.setHeaders(hdrs);

        // Setup a callback to be fired when a message or an event (open, close, error) is received
        client.setMessageCallback([this](const std::string& message) {
            if (this->isCleanedUp()) return;
            eventCallback(MESSAGE, message.c_str());
        });

        client.setOpenCallback([this]() {
            cJSON *root;
            root = cJSON_CreateObject();
            cJSON_AddStringToObject(root, "status", "connected");
            char *json_str = cJSON_PrintUnformatted(root);
            eventCallback(CONNECT_SUCCESS, json_str);
            cJSON_Delete(root);
            switch_safe_free(json_str);
        });

        client.setErrorCallback([this](int code, const std::string &msg) {
            cJSON *root, *message;
            root = cJSON_CreateObject();
            cJSON_AddStringToObject(root, "status", "error");
            message = cJSON_CreateObject();
            cJSON_AddNumberToObject(message, "code", code);
            cJSON_AddStringToObject(message, "error", msg.c_str());
            cJSON_AddItemToObject(root, "message", message);

            char *json_str = cJSON_PrintUnformatted(root);

            eventCallback(CONNECT_ERROR, json_str);

            cJSON_Delete(root);
            switch_safe_free(json_str);
        });

        client.setCloseCallback([this](int code, const std::string &reason) {
            cJSON *root, *message;
            root = cJSON_CreateObject();
            cJSON_AddStringToObject(root, "status", "disconnected");
            message = cJSON_CreateObject();
            cJSON_AddNumberToObject(message, "code", code);
            cJSON_AddStringToObject(message, "reason", reason.c_str());
            cJSON_AddItemToObject(root, "message", message);
            char *json_str = cJSON_PrintUnformatted(root);

            eventCallback(CONNECTION_DROPPED, json_str);

            cJSON_Delete(root);
            switch_safe_free(json_str);
        });

        // Now that our callback is setup, we can start our background thread and receive messages
        client.connect();
    }

    switch_media_bug_t *get_media_bug(switch_core_session_t *session) {
        switch_channel_t *channel = switch_core_session_get_channel(session);
        if(!channel) {
            return nullptr;
        }
        auto *bug = (switch_media_bug_t *) switch_channel_get_private(channel, MY_BUG_NAME);
        return bug;
    }

    inline void media_bug_close(switch_core_session_t *session) {
        auto *bug = get_media_bug(session);
        if(bug) {
            auto* tech_pvt = (private_t*) switch_core_media_bug_get_user_data(bug);
            tech_pvt->close_requested = 1;
            switch_core_media_bug_close(&bug, SWITCH_FALSE);
        }
    }

    inline void send_initial_metadata(switch_core_session_t *session) {
        auto *bug = get_media_bug(session);
        if(bug) {
            auto* tech_pvt = (private_t*) switch_core_media_bug_get_user_data(bug);
            if(tech_pvt && strlen(tech_pvt->initialMetadata) > 0) {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG,
                                          "sending initial metadata %s\n", tech_pvt->initialMetadata);
                writeText(tech_pvt->initialMetadata);
            }
        }
    }

    void eventCallback(notifyEvent_t event, const char* message) {
        switch_core_session_t* psession = switch_core_session_locate(m_sessionId.c_str());
        if(psession) {
            switch (event) {
                case CONNECT_SUCCESS:
                    send_initial_metadata(psession);
                    m_notify(psession, EVENT_CONNECT, message);
                    break;
                case CONNECTION_DROPPED:
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(psession), SWITCH_LOG_INFO, "connection closed\n");
                    m_notify(psession, EVENT_DISCONNECT, message);
                    break;
                case CONNECT_ERROR:
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(psession), SWITCH_LOG_INFO, "connection error\n");
                    m_notify(psession, EVENT_ERROR, message);

                    media_bug_close(psession);

                    break;
                case MESSAGE:
                    std::string msg(message);
                    if(processMessage(psession, msg) != SWITCH_TRUE) {
                        m_notify(psession, EVENT_JSON, msg.c_str());
                    }
                    if(!m_suppress_log)
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(psession), SWITCH_LOG_DEBUG, "response: %s\n", msg.c_str());
                    break;
            }
            switch_core_session_rwunlock(psession);
        }
    }

    switch_bool_t processMessage(switch_core_session_t* session, std::string& message) {
        cJSON* json = cJSON_Parse(message.c_str());
        switch_bool_t status = SWITCH_FALSE;
        if (!json) {
            return status;
        }
        
        /* Get tech_pvt for playback buffer access 
         * The channel stores the media bug, not tech_pvt directly.
         * We need to get the bug first, then extract user_data.
         */
        switch_channel_t *channel = switch_core_session_get_channel(session);
        switch_media_bug_t *bug = (switch_media_bug_t *)switch_channel_get_private(channel, MY_BUG_NAME);
        private_t *tech_pvt = NULL;
        if (bug) {
            tech_pvt = (private_t *)switch_core_media_bug_get_user_data(bug);
        }
        
        const char* jsType = cJSON_GetObjectCstr(json, "type");
        
        // NETPLAY: stopAudio - clear playback buffer (barge-in)
        if(jsType && strcmp(jsType, "stopAudio") == 0) {
            if (tech_pvt && tech_pvt->playback_buffer) {
                switch_mutex_lock(tech_pvt->playback_mutex);
                switch_buffer_zero(tech_pvt->playback_buffer);
                tech_pvt->playback_active = 0;
                switch_mutex_unlock(tech_pvt->playback_mutex);
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO,
                    "(%s) [PLAYBACK] stopped (barge-in)\n", m_sessionId.c_str());
            }
            status = SWITCH_TRUE;
        }
        // NETPLAY v2.0: streamAudio - write directly to playback buffer (true streaming)
        else if(jsType && strcmp(jsType, "streamAudio") == 0) {
            cJSON* jsonData = cJSON_GetObjectItem(json, "data");
            if(jsonData && tech_pvt && tech_pvt->playback_buffer) {
                cJSON* jsonAudio = cJSON_DetachItemFromObject(jsonData, "audioData");
                const char* jsAudioDataType = cJSON_GetObjectCstr(jsonData, "audioDataType");
                
                if (jsAudioDataType && strcmp(jsAudioDataType, "raw") == 0 && jsonAudio && jsonAudio->valuestring) {
                    std::string rawAudio;
                    try {
                        rawAudio = base64_decode(jsonAudio->valuestring);
                    } catch (const std::exception& e) {
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, 
                            "(%s) base64 decode error: %s\n", m_sessionId.c_str(), e.what());
                        cJSON_Delete(jsonAudio); 
                        cJSON_Delete(json);
                        return status;
                    }
                    
                    if (rawAudio.size() < 2) {
                        cJSON_Delete(jsonAudio);
                        cJSON_Delete(json);
                        return status;
                    }
                    if (rawAudio.size() % 2 != 0) {
                        rawAudio.pop_back();
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session),
                            get_stream_log_level(session, SWITCH_LOG_DEBUG),
                            "(%s) [BUFFER] ajuste: payload com tamanho ímpar, truncando 1B\n",
                            m_sessionId.c_str());
                    }

                    switch_mutex_lock(tech_pvt->playback_mutex);
                    if (tech_pvt->first_audio_ts == 0) {
                        tech_pvt->first_audio_ts = switch_micro_time_now();
                    }
                    
                    /* Check for buffer overrun - if near full, discard oldest data */
                    const switch_size_t buffer_capacity = tech_pvt->playback_buflen ? tech_pvt->playback_buflen : 32000;
                    const switch_size_t high_water_mark = buffer_capacity > rawAudio.size()
                        ? (buffer_capacity - rawAudio.size())
                        : 0;
                    switch_size_t current_size = switch_buffer_inuse(tech_pvt->playback_buffer);
                    
                    if (rawAudio.size() > buffer_capacity) {
                        switch_buffer_zero(tech_pvt->playback_buffer);
                        const size_t offset = rawAudio.size() - buffer_capacity;
                        switch_buffer_write(tech_pvt->playback_buffer, rawAudio.data() + offset, buffer_capacity);
                        tech_pvt->buffer_overruns++;
                        current_size = switch_buffer_inuse(tech_pvt->playback_buffer);
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_WARNING,
                            "(%s) [BUFFER] overrun: payload %zuB > buffer %zuB, truncating\n",
                            m_sessionId.c_str(), rawAudio.size(), buffer_capacity);
                    } else if (current_size > high_water_mark) {
                        /* Buffer nearly full - discard oldest data to make room */
                        switch_size_t to_discard = current_size - high_water_mark + rawAudio.size();
                        char discard_buf[1024];
                        while (to_discard > 0) {
                            switch_size_t chunk = (to_discard > sizeof(discard_buf)) ? sizeof(discard_buf) : to_discard;
                            switch_buffer_read(tech_pvt->playback_buffer, discard_buf, chunk);
                            to_discard -= chunk;
                        }
                        tech_pvt->buffer_overruns++;
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_WARNING,
                            "(%s) [BUFFER] overrun: discarded %zuB (capacity=%zuB, used=%zuB)\n",
                            m_sessionId.c_str(), (size_t)(current_size - high_water_mark + rawAudio.size()),
                            buffer_capacity, current_size);
                    }
                    
                    /* Write new audio to buffer */
                    if (rawAudio.size() <= buffer_capacity) {
                        switch_buffer_write(tech_pvt->playback_buffer, rawAudio.data(), rawAudio.size());
                    }
                    
                    switch_size_t buffered = switch_buffer_inuse(tech_pvt->playback_buffer);
                    if (buffered > tech_pvt->buffer_max_used) {
                        tech_pvt->buffer_max_used = buffered;
                    }
                    switch_mutex_unlock(tech_pvt->playback_mutex);
                    
                    /* Log every 50 chunks or on significant events */
                    static int chunk_count = 0;
                    if (++chunk_count % 50 == 1 || buffered < 1000) {
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session),
                            get_stream_log_level(session, SWITCH_LOG_DEBUG),
                            "(%s) [BUFFER] +%zuB (total=%zuB, active=%d)\n",
                            m_sessionId.c_str(), rawAudio.size(), buffered, tech_pvt->playback_active);
                    }
                    
                    status = SWITCH_TRUE;
                }
                
                if (jsonAudio)
                    cJSON_Delete(jsonAudio);
            } else {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, 
                    "(%s) streamAudio - missing data or buffer\n", m_sessionId.c_str());
            }
        }
        cJSON_Delete(json);
        return status;
    }

    ~AudioStreamer()= default;

    void disconnect() {
        switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_DEBUG, "disconnecting...\n");
        client.disconnect();
    }

    bool isConnected() {
        return client.isConnected();
    }

    void writeBinary(uint8_t* buffer, size_t len) {
        if(!this->isConnected()) return;
        client.sendBinary(buffer, len);
    }

    void writeText(const char* text) {
        if(!this->isConnected()) return;
        client.sendMessage(text, strlen(text));
    }

    void deleteFiles() {
        if(m_playFile >0) {
            for (const auto &fileName: m_Files) {
                remove(fileName.c_str());
            }
        }
    }

    void markCleanedUp() {
        m_cleanedUp.store(true, std::memory_order_release);
        // clear callbacks to prevent dangling calls
        client.setMessageCallback({});
    }

    bool isCleanedUp() const {
        return m_cleanedUp.load(std::memory_order_acquire);
    }

private:
    std::string m_sessionId;
    responseHandler_t m_notify;
    WebSocketClient client;
    bool m_suppress_log;
    const char* m_extra_headers;
    int m_playFile;
    std::unordered_set<std::string> m_Files;
    std::atomic<bool> m_cleanedUp{false};
};


namespace {

    switch_status_t stream_data_init(private_t *tech_pvt, switch_core_session_t *session, char *wsUri,
                                     uint32_t sampling, int desiredSampling, int channels, int audio_format, char *metadata, responseHandler_t responseHandler,
                                     int deflate, int heart_beat, bool suppressLog, int rtp_packets, const char* extra_headers,
                                     bool no_reconnect, const char *tls_cafile, const char *tls_keyfile,
                                     const char *tls_certfile, bool tls_disable_hostname_validation)
    {
        int err; //speex

        switch_memory_pool_t *pool = switch_core_session_get_pool(session);

        memset(tech_pvt, 0, sizeof(private_t));

        /* Use strncpy with explicit null-termination to prevent buffer overread */
        strncpy(tech_pvt->sessionId, switch_core_session_get_uuid(session), MAX_SESSION_ID - 1);
        tech_pvt->sessionId[MAX_SESSION_ID - 1] = '\0';
        strncpy(tech_pvt->ws_uri, wsUri, MAX_WS_URI - 1);
        tech_pvt->ws_uri[MAX_WS_URI - 1] = '\0';
        tech_pvt->sampling = desiredSampling;
        tech_pvt->responseHandler = responseHandler;
        tech_pvt->rtp_packets = rtp_packets;
        tech_pvt->channels = channels;
        tech_pvt->audio_paused = 0;
        tech_pvt->audio_format = audio_format;
        tech_pvt->codec_initialized = 0;

        if (metadata) {
            strncpy(tech_pvt->initialMetadata, metadata, MAX_METADATA_LEN - 1);
            tech_pvt->initialMetadata[MAX_METADATA_LEN - 1] = '\0';
        }

        /* Calculate buffer length with overflow protection
         * Formula: FRAME_SIZE_8000 * (desiredSampling / 8000) * channels * rtp_packets
         * FRAME_SIZE_8000 = 320 bytes (20ms @ 8kHz stereo L16)
         * Max reasonable values: 48kHz, 2 channels, 10 rtp_packets = 320 * 6 * 2 * 10 = 38400
         */
        const size_t buflen = ((size_t)FRAME_SIZE_8000 * (size_t)desiredSampling / 8000) * 
                              (size_t)channels * (size_t)rtp_packets;

        auto* as = new AudioStreamer(tech_pvt->sessionId, wsUri, responseHandler, deflate, heart_beat,
                                        suppressLog, extra_headers, no_reconnect,
                                        tls_cafile, tls_keyfile, tls_certfile, tls_disable_hostname_validation);

        tech_pvt->pAudioStreamer = static_cast<void *>(as);

        switch_mutex_init(&tech_pvt->mutex, SWITCH_MUTEX_NESTED, pool);
        
        if (switch_buffer_create(pool, &tech_pvt->sbuffer, buflen) != SWITCH_STATUS_SUCCESS) {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                "%s: Error creating switch buffer.\n", tech_pvt->sessionId);
            return SWITCH_STATUS_FALSE;
        }
        
        /* NETPLAY: Create playback buffer for streaming audio from WebSocket */
        /* Buffer size default: 2 seconds of L16 audio @ 8kHz = 32000 bytes */
        switch_channel_t *channel = switch_core_session_get_channel(session);
        const char *buffer_ms_str = switch_channel_get_variable(channel, "STREAM_PLAYBACK_BUFFER_MS");
        const char *warmup_ms_str = switch_channel_get_variable(channel, "STREAM_PLAYBACK_WARMUP_MS");
        const char *low_water_ms_str = switch_channel_get_variable(channel, "STREAM_PLAYBACK_LOW_WATER_MS");
        int buffer_ms = buffer_ms_str ? atoi(buffer_ms_str) : 2000;
        int warmup_ms = warmup_ms_str ? atoi(warmup_ms_str) : 400;
        int low_water_ms = low_water_ms_str ? atoi(low_water_ms_str) : 160;
        if (buffer_ms < 200) buffer_ms = 200;
        if (buffer_ms > 10000) buffer_ms = 10000;
        if (warmup_ms < 40) warmup_ms = 40;
        if (low_water_ms < 40) low_water_ms = 40;
        if (warmup_ms >= buffer_ms) warmup_ms = buffer_ms / 2;
        if (low_water_ms >= buffer_ms) low_water_ms = buffer_ms / 4;
        const size_t playback_buflen = (size_t)buffer_ms * 16; /* 8kHz L16 = 16 bytes/ms */
        if (switch_buffer_create(pool, &tech_pvt->playback_buffer, playback_buflen) != SWITCH_STATUS_SUCCESS) {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                "%s: Error creating playback buffer.\n", tech_pvt->sessionId);
            return SWITCH_STATUS_FALSE;
        }
        switch_mutex_init(&tech_pvt->playback_mutex, SWITCH_MUTEX_NESTED, pool);
        tech_pvt->playback_active = 0;
        tech_pvt->playback_buflen = playback_buflen;
        tech_pvt->warmup_threshold = (switch_size_t)warmup_ms * 16;
        tech_pvt->low_water_mark = (switch_size_t)low_water_ms * 16;
        tech_pvt->first_audio_ts = 0;
        tech_pvt->playback_start_ts = 0;
        tech_pvt->buffer_overruns = 0;
        tech_pvt->buffer_underruns = 0;
        tech_pvt->buffer_max_used = 0;
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO,
            "(%s) [PLAYBACK] buffer created (%zuB, warmup=%dms, low_water=%dms)\n",
            tech_pvt->sessionId, playback_buflen, warmup_ms, low_water_ms);

        if (desiredSampling != sampling) {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "(%s) resampling from %u to %u\n", tech_pvt->sessionId, sampling, desiredSampling);
            tech_pvt->resampler = speex_resampler_init(channels, sampling, desiredSampling, SWITCH_RESAMPLE_QUALITY, &err);
            if (0 != err) {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "Error initializing resampler: %s.\n", speex_resampler_strerror(err));
                return SWITCH_STATUS_FALSE;
            }
        }
        else {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "(%s) no resampling needed for this call\n", tech_pvt->sessionId);
        }

        /* Initialize G.711 codec if requested */
        if (audio_format == AUDIO_FORMAT_PCMU || audio_format == AUDIO_FORMAT_PCMA) {
            const char *codec_name = (audio_format == AUDIO_FORMAT_PCMU) ? "PCMU" : "PCMA";
            
            /* Defensive check: G.711 requires 8kHz sample rate */
            if (desiredSampling != 8000) {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                    "(%s) G.711 (%s) requires 8kHz sample rate, got %d Hz\n", 
                    tech_pvt->sessionId, codec_name, desiredSampling);
                /* Clean up AudioStreamer before returning to prevent memory leak */
                if (tech_pvt->pAudioStreamer) {
                    auto* as = static_cast<AudioStreamer*>(tech_pvt->pAudioStreamer);
                    as->markCleanedUp();
                    as->disconnect();
                    delete as;
                    tech_pvt->pAudioStreamer = nullptr;
                }
                return SWITCH_STATUS_FALSE;
            }
            
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, 
                "(%s) Initializing %s codec for G.711 encoding\n", tech_pvt->sessionId, codec_name);
            
            if (switch_core_codec_init(&tech_pvt->write_codec,
                                       codec_name,
                                       NULL,
                                       NULL,
                                       8000,      /* G.711 is always 8kHz */
                                       20,        /* 20ms ptime */
                                       channels,
                                       SWITCH_CODEC_FLAG_ENCODE,
                                       NULL,
                                       pool) != SWITCH_STATUS_SUCCESS) {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                    "(%s) Failed to initialize %s codec\n", tech_pvt->sessionId, codec_name);
                /* Clean up AudioStreamer before returning to prevent memory leak */
                if (tech_pvt->pAudioStreamer) {
                    auto* as = static_cast<AudioStreamer*>(tech_pvt->pAudioStreamer);
                    as->markCleanedUp();
                    as->disconnect();
                    delete as;
                    tech_pvt->pAudioStreamer = nullptr;
                }
                return SWITCH_STATUS_FALSE;
            }
            tech_pvt->codec_initialized = 1;
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, 
                "(%s) %s codec initialized successfully\n", tech_pvt->sessionId, codec_name);
        }

        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "(%s) stream_data_init\n", tech_pvt->sessionId);

        return SWITCH_STATUS_SUCCESS;
    }

    void destroy_tech_pvt(private_t* tech_pvt) {
        switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_INFO, "%s destroy_tech_pvt\n", tech_pvt->sessionId);
        if (tech_pvt->resampler) {
            speex_resampler_destroy(tech_pvt->resampler);
            tech_pvt->resampler = nullptr;
        }
        if (tech_pvt->codec_initialized) {
            switch_core_codec_destroy(&tech_pvt->write_codec);
            tech_pvt->codec_initialized = 0;
        }
        if (tech_pvt->mutex) {
            switch_mutex_destroy(tech_pvt->mutex);
            tech_pvt->mutex = nullptr;
        }
        if (tech_pvt->playback_buffer) {
            switch_buffer_destroy(&tech_pvt->playback_buffer);
            tech_pvt->playback_buffer = nullptr;
        }
        if (tech_pvt->playback_mutex) {
            switch_mutex_destroy(tech_pvt->playback_mutex);
            tech_pvt->playback_mutex = nullptr;
        }
        /*if (tech_pvt->pAudioStreamer) {
            auto* as = (AudioStreamer *) tech_pvt->pAudioStreamer;
            delete as;
            tech_pvt->pAudioStreamer = nullptr;
        }*/
    }

    void finish(AudioStreamer* audioStreamer) {
        audioStreamer->markCleanedUp();
        audioStreamer->disconnect();
        delete audioStreamer;
    }

}

extern "C" {
    int validate_ws_uri(const char* url, char* wsUri) {
        const char* hostStart = nullptr;
        const char* hostEnd = nullptr;
        const char* portStart = nullptr;

        // Check scheme (ws:// or wss://)
        if (strncmp(url, "ws://", 5) == 0) {
            hostStart = url + 5;
        } else if (strncmp(url, "wss://", 6) == 0) {
            hostStart = url + 6;
        } else {
            return 0;
        }

        // Find host end or port start
        hostEnd = hostStart;
        while (*hostEnd && *hostEnd != ':' && *hostEnd != '/') {
            if (!std::isalnum(*hostEnd) && *hostEnd != '-' && *hostEnd != '.') {
                return 0;
            }
            ++hostEnd;
        }

        // Check if host is empty
        if (hostStart == hostEnd) {
            return 0;
        }

        // Check for port
        if (*hostEnd == ':') {
            portStart = hostEnd + 1;
            while (*portStart && *portStart != '/') {
                if (!std::isdigit(*portStart)) {
                    return 0;
                }
                ++portStart;
            }
        }

        // Copy valid URI to wsUri
        std::strncpy(wsUri, url, MAX_WS_URI);
        return 1;
    }

    switch_status_t is_valid_utf8(const char *str) {
        switch_status_t status = SWITCH_STATUS_FALSE;
        while (*str) {
            if ((*str & 0x80) == 0x00) {
                // 1-byte character
                str++;
            } else if ((*str & 0xE0) == 0xC0) {
                // 2-byte character
                if ((str[1] & 0xC0) != 0x80) {
                    return status;
                }
                str += 2;
            } else if ((*str & 0xF0) == 0xE0) {
                // 3-byte character
                if ((str[1] & 0xC0) != 0x80 || (str[2] & 0xC0) != 0x80) {
                    return status;
                }
                str += 3;
            } else if ((*str & 0xF8) == 0xF0) {
                // 4-byte character
                if ((str[1] & 0xC0) != 0x80 || (str[2] & 0xC0) != 0x80 || (str[3] & 0xC0) != 0x80) {
                    return status;
                }
                str += 4;
            } else {
                // invalid character
                return status;
            }
        }
        return SWITCH_STATUS_SUCCESS;
    }

    switch_status_t stream_session_send_text(switch_core_session_t *session, char* text) {
        switch_channel_t *channel = switch_core_session_get_channel(session);
        auto *bug = (switch_media_bug_t*) switch_channel_get_private(channel, MY_BUG_NAME);
        if (!bug) {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "stream_session_send_text failed because no bug\n");
            return SWITCH_STATUS_FALSE;
        }
        auto *tech_pvt = (private_t*) switch_core_media_bug_get_user_data(bug);

        if (!tech_pvt) return SWITCH_STATUS_FALSE;
        auto *pAudioStreamer = static_cast<AudioStreamer *>(tech_pvt->pAudioStreamer);
        if (pAudioStreamer && text) pAudioStreamer->writeText(text);

        return SWITCH_STATUS_SUCCESS;
    }

    switch_status_t stream_session_pauseresume(switch_core_session_t *session, int pause) {
        switch_channel_t *channel = switch_core_session_get_channel(session);
        auto *bug = (switch_media_bug_t*) switch_channel_get_private(channel, MY_BUG_NAME);
        if (!bug) {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "stream_session_pauseresume failed because no bug\n");
            return SWITCH_STATUS_FALSE;
        }
        auto *tech_pvt = (private_t*) switch_core_media_bug_get_user_data(bug);

        if (!tech_pvt) return SWITCH_STATUS_FALSE;

        switch_core_media_bug_flush(bug);
        tech_pvt->audio_paused = pause;
        return SWITCH_STATUS_SUCCESS;
    }

    switch_status_t stream_session_init(switch_core_session_t *session,
                                        responseHandler_t responseHandler,
                                        uint32_t samples_per_second,
                                        char *wsUri,
                                        int sampling,
                                        int channels,
                                        int audio_format,
                                        char* metadata,
                                        void **ppUserData)
    {
        int deflate = 0;
        int heart_beat = 0;
        bool suppressLog = false;
        const char* buffer_size = NULL;
        const char* extra_headers = NULL;
        int rtp_packets = 1; //20ms burst
        bool no_reconnect = false;
        const char* tls_cafile = NULL;
        const char* tls_keyfile = NULL;
        const char* tls_certfile = NULL;
        bool tls_disable_hostname_validation = false;

        switch_channel_t *channel = switch_core_session_get_channel(session);

        if (switch_channel_var_true(channel, "STREAM_MESSAGE_DEFLATE")) {
            deflate = 1;
        }

        if (switch_channel_var_true(channel, "STREAM_SUPPRESS_LOG")) {
            suppressLog = true;
        }

        if (switch_channel_var_true(channel, "STREAM_NO_RECONNECT")) {
            no_reconnect = true;
        }

        tls_cafile = switch_channel_get_variable(channel, "STREAM_TLS_CA_FILE");
        tls_keyfile = switch_channel_get_variable(channel, "STREAM_TLS_KEY_FILE");
        tls_certfile = switch_channel_get_variable(channel, "STREAM_TLS_CERT_FILE");

        if (switch_channel_var_true(channel, "STREAM_TLS_DISABLE_HOSTNAME_VALIDATION")) {
            tls_disable_hostname_validation = true;
        }

        const char* heartBeat = switch_channel_get_variable(channel, "STREAM_HEART_BEAT");
        if (heartBeat) {
            char *endptr;
            long value = strtol(heartBeat, &endptr, 10);
            if (*endptr == '\0' && value <= INT_MAX && value >= INT_MIN) {
                heart_beat = (int) value;
            }
        }

        if ((buffer_size = switch_channel_get_variable(channel, "STREAM_BUFFER_SIZE"))) {
            int bSize = atoi(buffer_size);
            if(bSize % 20 != 0) {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_WARNING, "%s: Buffer size of %s is not a multiple of 20ms. Using default 20ms.\n",
                                  switch_channel_get_name(channel), buffer_size);
            } else if(bSize >= 20){
                rtp_packets = bSize/20;
            }
        }

        extra_headers = switch_channel_get_variable(channel, "STREAM_EXTRA_HEADERS");

        // allocate per-session tech_pvt
        auto* tech_pvt = (private_t *) switch_core_session_alloc(session, sizeof(private_t));

        if (!tech_pvt) {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "error allocating memory!\n");
            return SWITCH_STATUS_FALSE;
        }
        if (SWITCH_STATUS_SUCCESS != stream_data_init(tech_pvt, session, wsUri, samples_per_second, sampling, channels, audio_format, metadata, responseHandler, deflate, heart_beat,
                                                        suppressLog, rtp_packets, extra_headers, no_reconnect, tls_cafile, tls_keyfile, tls_certfile, tls_disable_hostname_validation)) {
            destroy_tech_pvt(tech_pvt);
            return SWITCH_STATUS_FALSE;
        }

        *ppUserData = tech_pvt;

        return SWITCH_STATUS_SUCCESS;
    }

    /* Helper function to encode L16 PCM to G.711 
     * 
     * Note: G.711 requires 8kHz audio. If input is not 8kHz, encoding will fail.
     * The caller must ensure proper sample rate before calling this function.
     * 
     * L16 @ 8kHz: 160 samples = 320 bytes per 20ms frame
     * G.711 @ 8kHz: 160 samples = 160 bytes per 20ms frame (1 byte per sample)
     */
    static size_t encode_g711(private_t *tech_pvt, const uint8_t *pcm_data, size_t pcm_len, uint8_t *g711_data, size_t g711_buflen) {
        if (!tech_pvt || !tech_pvt->codec_initialized) {
            switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_WARNING, 
                "encode_g711: codec not initialized\n");
            return 0;
        }
        
        if (!pcm_data || pcm_len == 0) {
            return 0;
        }
        
        if (pcm_len % 2 != 0) {
            switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_WARNING,
                "encode_g711: odd PCM length %zu\n", pcm_len);
            return 0;
        }
        
        if (tech_pvt->rtp_packets == 1 && (pcm_len % 320 != 0)) {
            switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_DEBUG,
                "encode_g711: non-20ms PCM length %zu\n", pcm_len);
        }
        
        /* Validate buffer size: G.711 output is half the size of L16 input */
        size_t expected_output = pcm_len / 2;
        if (g711_buflen < expected_output) {
            switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_ERROR, 
                "encode_g711: output buffer too small (%zu < %zu)\n", g711_buflen, expected_output);
            return 0;
        }
        
        if (tech_pvt->audio_format == AUDIO_FORMAT_PCMU) {
            const int16_t *samples = (const int16_t *)pcm_data;
            const size_t sample_count = pcm_len / 2;
            for (size_t i = 0; i < sample_count; ++i) {
                g711_data[i] = linear_to_ulaw(samples[i]);
            }
            return sample_count;
        }
        
        uint32_t encoded_len = (uint32_t)g711_buflen;
        uint32_t encoded_rate = 8000;
        unsigned int flags = 0;
        
        switch_status_t status = switch_core_codec_encode(
            &tech_pvt->write_codec,
            NULL,
            (void *)pcm_data,
            (uint32_t)pcm_len,
            8000,  /* G.711 is always 8kHz */
            g711_data,
            &encoded_len,
            &encoded_rate,
            &flags
        );
        
        if (status != SWITCH_STATUS_SUCCESS) {
            switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_ERROR, 
                "encode_g711: switch_core_codec_encode failed (pcm_len=%zu)\n", pcm_len);
            return 0;
        }
        
        return (size_t)encoded_len;
    }

    switch_bool_t stream_frame(switch_media_bug_t *bug) {
        auto *tech_pvt = (private_t *)switch_core_media_bug_get_user_data(bug);
        if (!tech_pvt || tech_pvt->audio_paused) return SWITCH_TRUE;
        
        /* NETPLAY v2.5: Full-duplex mode - AEC no Python
         * 
         * O half-duplex foi removido. O cancelamento de eco agora é feito no Python
         * usando Speex DSP (EchoCancellerWrapper em realtime/utils/echo_canceller.py).
         * 
         * Fluxo:
         * 1. FreeSWITCH envia mic + echo para Python
         * 2. Python usa speaker buffer como referência para AEC
         * 3. Python envia audio limpo para OpenAI
         * 
         * Isso permite barge-in real durante a fala do agente.
         */
        /*
        auto flush_sbuffer = [&]() {
            switch_size_t inuse = switch_buffer_inuse(tech_pvt->sbuffer);
            if (inuse > 0) {
                std::vector<uint8_t> tmp(inuse);
                switch_buffer_read(tech_pvt->sbuffer, tmp.data(), inuse);
                switch_buffer_zero(tech_pvt->sbuffer);
                pAudioStreamer->writeBinary(tmp.data(), inuse);
            }
        };
        */
        if (switch_mutex_trylock(tech_pvt->mutex) == SWITCH_STATUS_SUCCESS) {

            if (!tech_pvt->pAudioStreamer) {
                switch_mutex_unlock(tech_pvt->mutex);
                return SWITCH_TRUE;
            }

            auto *pAudioStreamer = static_cast<AudioStreamer *>(tech_pvt->pAudioStreamer);

            if (!pAudioStreamer->isConnected()) {
                switch_mutex_unlock(tech_pvt->mutex);
                return SWITCH_TRUE;
            }

            /* Check if we need G.711 encoding */
            const bool use_g711 = (tech_pvt->audio_format == AUDIO_FORMAT_PCMU || tech_pvt->audio_format == AUDIO_FORMAT_PCMA);

            if (nullptr == tech_pvt->resampler) {
                
                uint8_t data_buf[SWITCH_RECOMMENDED_BUFFER_SIZE];
                uint8_t g711_buf[SWITCH_RECOMMENDED_BUFFER_SIZE / 2]; /* G.711 is half the size of L16 */
                /* Pre-allocate buffer for reading from sbuffer to avoid allocation in hot path */
                uint8_t sbuffer_tmp[SWITCH_RECOMMENDED_BUFFER_SIZE];
                uint8_t sbuffer_g711[SWITCH_RECOMMENDED_BUFFER_SIZE / 2];
                
                switch_frame_t frame = {0};
                frame.data = data_buf;
                frame.buflen = SWITCH_RECOMMENDED_BUFFER_SIZE;
                size_t available = switch_buffer_freespace(tech_pvt->sbuffer);

                while (switch_core_media_bug_read(bug, &frame, SWITCH_TRUE) == SWITCH_STATUS_SUCCESS) {
                    if (frame.datalen) {
                        if (1 == tech_pvt->rtp_packets) {
                            if (use_g711 && tech_pvt->codec_initialized) {
                                /* Encode L16 to G.711 before sending */
                                size_t g711_len = encode_g711(tech_pvt, (uint8_t *)frame.data, frame.datalen, g711_buf, sizeof(g711_buf));
                                if (g711_len > 0) {
                                    pAudioStreamer->writeBinary(g711_buf, g711_len);
                                }
                            } else {
                                pAudioStreamer->writeBinary((uint8_t *) frame.data, frame.datalen);
                            }
                            continue;
                        }
                        if (available >= frame.datalen) {
                            switch_buffer_write(tech_pvt->sbuffer, static_cast<uint8_t *>(frame.data), frame.datalen);
                        }
                        if (0 == switch_buffer_freespace(tech_pvt->sbuffer)) {
                            switch_size_t inuse = switch_buffer_inuse(tech_pvt->sbuffer);
                            if (inuse > 0 && inuse <= sizeof(sbuffer_tmp)) {
                                switch_buffer_read(tech_pvt->sbuffer, sbuffer_tmp, inuse);
                                switch_buffer_zero(tech_pvt->sbuffer);
                                
                                if (use_g711 && tech_pvt->codec_initialized) {
                                    /* Encode buffered L16 to G.711 before sending */
                                    size_t g711_len = encode_g711(tech_pvt, sbuffer_tmp, inuse, sbuffer_g711, sizeof(sbuffer_g711));
                                    if (g711_len > 0) {
                                        pAudioStreamer->writeBinary(sbuffer_g711, g711_len);
                                    }
                                } else {
                                    pAudioStreamer->writeBinary(sbuffer_tmp, inuse);
                                }
                            }
                        }
                    }
                }
                
            } else {

                uint8_t data[SWITCH_RECOMMENDED_BUFFER_SIZE];
                uint8_t g711_buf[SWITCH_RECOMMENDED_BUFFER_SIZE / 2];
                /* Pre-allocate buffers to avoid allocation in hot path */
                uint8_t sbuffer_tmp[SWITCH_RECOMMENDED_BUFFER_SIZE];
                uint8_t sbuffer_g711[SWITCH_RECOMMENDED_BUFFER_SIZE / 2];
                /* Pre-allocate resampler output buffer to avoid VLA (stack overflow risk) */
                spx_int16_t resampler_out[SWITCH_RECOMMENDED_BUFFER_SIZE / sizeof(spx_int16_t)];
                const size_t max_out_samples = sizeof(resampler_out) / sizeof(spx_int16_t);
                
                switch_frame_t frame = {};
                frame.data = data;
                frame.buflen = SWITCH_RECOMMENDED_BUFFER_SIZE;
                const size_t available = switch_buffer_freespace(tech_pvt->sbuffer);

                while (switch_core_media_bug_read(bug, &frame, SWITCH_TRUE) == SWITCH_STATUS_SUCCESS) {
                    if(frame.datalen) {
                        spx_uint32_t in_len = frame.samples;
                        spx_uint32_t out_len = (spx_uint32_t)(max_out_samples / tech_pvt->channels);
                        spx_int16_t *out = resampler_out;

                        if(tech_pvt->channels == 1) {
                            speex_resampler_process_int(tech_pvt->resampler,
                                            0,
                                            (const spx_int16_t *)frame.data,
                                            &in_len,
                                            &out[0],
                                            &out_len);
                        } else {
                            speex_resampler_process_interleaved_int(tech_pvt->resampler,
                                            (const spx_int16_t *)frame.data,
                                            &in_len,
                                            &out[0],
                                            &out_len);
                        }

                        if(out_len > 0) {
                            const size_t bytes_written = out_len * tech_pvt->channels * sizeof(spx_int16_t);
                            if (tech_pvt->rtp_packets == 1) { //20ms packet
                                if (use_g711 && tech_pvt->codec_initialized) {
                                    /* Encode resampled L16 to G.711 before sending */
                                    size_t g711_len = encode_g711(tech_pvt, (uint8_t *)out, bytes_written, g711_buf, sizeof(g711_buf));
                                    if (g711_len > 0) {
                                        pAudioStreamer->writeBinary(g711_buf, g711_len);
                                    }
                                } else {
                                    pAudioStreamer->writeBinary((uint8_t *) out, bytes_written);
                                }
                                continue;
                            }
                            if (bytes_written <= available) {
                                switch_buffer_write(tech_pvt->sbuffer, (const uint8_t *)out, bytes_written);
                            }
                        }

                        if(switch_buffer_freespace(tech_pvt->sbuffer) == 0) {
                            switch_size_t inuse = switch_buffer_inuse(tech_pvt->sbuffer);
                            if (inuse > 0 && inuse <= sizeof(sbuffer_tmp)) {
                                switch_buffer_read(tech_pvt->sbuffer, sbuffer_tmp, inuse);
                                switch_buffer_zero(tech_pvt->sbuffer);
                                
                                if (use_g711 && tech_pvt->codec_initialized) {
                                    /* Encode buffered L16 to G.711 before sending */
                                    size_t g711_len = encode_g711(tech_pvt, sbuffer_tmp, inuse, sbuffer_g711, sizeof(sbuffer_g711));
                                    if (g711_len > 0) {
                                        pAudioStreamer->writeBinary(sbuffer_g711, g711_len);
                                    }
                                } else {
                                    pAudioStreamer->writeBinary(sbuffer_tmp, inuse);
                                }
                            }
                        }
                    }
                }
            }
            
            switch_mutex_unlock(tech_pvt->mutex);
        }

        return SWITCH_TRUE;
    }

    switch_status_t stream_session_cleanup(switch_core_session_t *session, char* text, int channelIsClosing) {
        switch_channel_t *channel = switch_core_session_get_channel(session);
        auto *bug = (switch_media_bug_t*) switch_channel_get_private(channel, MY_BUG_NAME);
        if(bug)
        {
            auto* tech_pvt = (private_t*) switch_core_media_bug_get_user_data(bug);
            char sessionId[MAX_SESSION_ID];
            /* Use strncpy with bounds check to prevent buffer overflow */
            strncpy(sessionId, tech_pvt->sessionId, MAX_SESSION_ID - 1);
            sessionId[MAX_SESSION_ID - 1] = '\0';
            AudioStreamer* audioStreamer = nullptr;

            switch_mutex_lock(tech_pvt->mutex);

            if (tech_pvt->cleanup_started) {
                switch_mutex_unlock(tech_pvt->mutex);
                return SWITCH_STATUS_SUCCESS;
            }
            tech_pvt->cleanup_started = 1;

            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "(%s) stream_session_cleanup\n", sessionId);

            switch_channel_set_private(channel, MY_BUG_NAME, nullptr);
            if (!channelIsClosing) {
                switch_core_media_bug_remove(session, &bug);
            }

            //auto* audioStreamer = (AudioStreamer *) tech_pvt->pAudioStreamer;
            audioStreamer = (AudioStreamer*) tech_pvt->pAudioStreamer;
            tech_pvt->pAudioStreamer = nullptr;

            switch_mutex_unlock(tech_pvt->mutex);

            if(audioStreamer) {
                audioStreamer->deleteFiles();
                if (text) audioStreamer->writeText(text);
                finish(audioStreamer);
            }

            destroy_tech_pvt(tech_pvt);

            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, "(%s) stream_session_cleanup: connection closed\n", sessionId);
            return SWITCH_STATUS_SUCCESS;
        }

        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "stream_session_cleanup: no bug - websocket connection already closed\n");
        return SWITCH_STATUS_FALSE;
    }
}

