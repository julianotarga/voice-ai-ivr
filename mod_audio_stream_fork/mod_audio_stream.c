/*
 * mod_audio_stream FreeSWITCH module to stream audio to websocket and receive response
 */
#include "mod_audio_stream.h"
#include "audio_streamer_glue.h"

SWITCH_MODULE_SHUTDOWN_FUNCTION(mod_audio_stream_shutdown);
SWITCH_MODULE_RUNTIME_FUNCTION(mod_audio_stream_runtime);
SWITCH_MODULE_LOAD_FUNCTION(mod_audio_stream_load);

SWITCH_MODULE_DEFINITION(mod_audio_stream, mod_audio_stream_load, mod_audio_stream_shutdown, NULL /*mod_audio_stream_runtime*/);

static void responseHandler(switch_core_session_t* session, const char* eventName, const char* json) {
    switch_event_t *event;
    switch_channel_t *channel = switch_core_session_get_channel(session);
    switch_event_create_subclass(&event, SWITCH_EVENT_CUSTOM, eventName);
    switch_channel_event_set_data(channel, event);
    if (json) switch_event_add_body(event, "%s", json);
    switch_event_fire(&event);
}

static switch_log_level_t get_stream_log_level(switch_core_session_t *session, switch_log_level_t default_level) {
    switch_channel_t *channel = switch_core_session_get_channel(session);
    const char *level = switch_channel_get_variable(channel, "STREAM_LOG_LEVEL");
    if (zstr(level)) return default_level;
    if (!strcasecmp(level, "ERROR")) return SWITCH_LOG_ERROR;
    if (!strcasecmp(level, "WARNING")) return SWITCH_LOG_WARNING;
    if (!strcasecmp(level, "INFO")) return SWITCH_LOG_INFO;
    if (!strcasecmp(level, "DEBUG")) return SWITCH_LOG_DEBUG;
    return default_level;
}

/*
 * Linear 16-bit PCM to μ-law conversion
 * Standard ITU-T G.711 algorithm
 */
static inline uint8_t linear_to_ulaw(int16_t pcm_val)
{
    static const int16_t BIAS = 0x84;   /* Bias for linear code */
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
    
    int sign, exponent, mantissa;
    uint8_t ulawbyte;
    
    /* Get the sign and the magnitude */
    sign = (pcm_val >> 8) & 0x80;
    if (sign != 0) pcm_val = -pcm_val;
    if (pcm_val > CLIP) pcm_val = CLIP;
    
    /* Convert from 16 bit linear to ulaw */
    pcm_val = pcm_val + BIAS;
    exponent = exp_lut[(pcm_val >> 7) & 0xFF];
    mantissa = (pcm_val >> (exponent + 3)) & 0x0F;
    ulawbyte = ~(sign | (exponent << 4) | mantissa);
    
    return ulawbyte;
}

static switch_bool_t capture_callback(switch_media_bug_t *bug, void *user_data, switch_abc_type_t type)
{
    switch_core_session_t *session = switch_core_media_bug_get_session(bug);
    private_t *tech_pvt = (private_t *)user_data;
    int channel_closing;

    switch (type) {
        case SWITCH_ABC_TYPE_INIT:
            break;

        case SWITCH_ABC_TYPE_CLOSE:
            {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, "Got SWITCH_ABC_TYPE_CLOSE.\n");
                if (tech_pvt) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO,
                        "[BUFFER] stats: overruns=%u underruns=%u max_used=%zuB\n",
                        tech_pvt->buffer_overruns,
                        tech_pvt->buffer_underruns,
                        tech_pvt->buffer_max_used);
                }
                // Check if this is a normal channel closure or a requested closure
                channel_closing = tech_pvt->close_requested ? 0 : 1;
                stream_session_cleanup(session, NULL, channel_closing);
            }
            break;

        case SWITCH_ABC_TYPE_READ:
            if (tech_pvt->close_requested) {
                return SWITCH_FALSE;
            }
            
            /* NETPLAY v2.1: Inject playback audio during READ callback
             * This is called every 20ms when receiving audio from caller.
             * We use this opportunity to also send audio TO the caller.
             */
            if (tech_pvt->playback_buffer && tech_pvt->playback_mutex) {
                switch_mutex_lock(tech_pvt->playback_mutex);
                
                switch_size_t available = switch_buffer_inuse(tech_pvt->playback_buffer);
                
                /* NETPLAY v2.7: PCMU Passthrough Support
                 * When playback_is_pcmu is set, buffer contains raw PCMU (160 bytes/frame)
                 * Otherwise, buffer contains L16 PCM (320 bytes/frame)
                 */
                const switch_size_t pcmu_frame_size = 160;  /* PCMU @ 8kHz, 20ms = 160 samples * 1 byte */
                const switch_size_t l16_frame_size = 320;   /* L16 @ 8kHz, 20ms = 160 samples * 2 bytes */
                const switch_size_t frame_size = tech_pvt->playback_is_pcmu ? pcmu_frame_size : l16_frame_size;
                
                /* NETPLAY v2.7: Increased buffer thresholds for PCMU passthrough
                 * 
                 * Problem: With OpenAI Realtime API, audio arrives in bursts with varying latency.
                 * Previous 400ms warmup was not enough to prevent underruns at end of phrases.
                 * 
                 * Solution:
                 * - Warmup threshold: 800ms (40 frames) - wait for more data before starting
                 * - Low water mark: 400ms (20 frames) - only pause if buffer gets critically low
                 * - This creates a larger "buffer zone" that absorbs latency spikes
                 * - Trade-off: ~800ms initial latency but eliminates robotization
                 */
                const switch_size_t warmup_threshold = tech_pvt->warmup_threshold ? tech_pvt->warmup_threshold : (frame_size * 40);
                const switch_size_t low_water_mark = tech_pvt->low_water_mark ? tech_pvt->low_water_mark : (frame_size * 20);
                
                /* Warmup: wait until we have enough buffer */
                if (!tech_pvt->playback_active && available >= warmup_threshold) {
                    tech_pvt->playback_active = 1;
                    tech_pvt->underrun_streak = 0;
                    tech_pvt->playback_start_ts = switch_micro_time_now();
                    if (tech_pvt->first_audio_ts > 0) {
                        uint64_t latency_ms = (tech_pvt->playback_start_ts - tech_pvt->first_audio_ts) / 1000;
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), get_stream_log_level(session, SWITCH_LOG_INFO),
                            "[PLAYBACK] started buffer=%zu bytes, latency=%" SWITCH_UINT64_T_FMT "ms, mode=%s\n",
                            available, latency_ms, tech_pvt->playback_is_pcmu ? "PCMU-passthrough" : "L16");
                    } else {
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), get_stream_log_level(session, SWITCH_LOG_INFO),
                            "[PLAYBACK] started buffer=%zu bytes, mode=%s\n", available,
                            tech_pvt->playback_is_pcmu ? "PCMU-passthrough" : "L16");
                    }
                }
                
                if (tech_pvt->playback_active && available >= frame_size) {
                    uint8_t pcmu_data[160]; /* 160 bytes of PCMU */
                    switch_codec_t *write_codec = switch_core_session_get_write_codec(session);
                    
                    if (tech_pvt->playback_is_pcmu) {
                        /* NETPLAY v2.7: PCMU Passthrough - read PCMU directly, no conversion */
                        switch_buffer_read(tech_pvt->playback_buffer, pcmu_data, pcmu_frame_size);
                        /* Data is already PCMU - write directly */
                    } else {
                        /* L16 mode - read L16 and convert to PCMU */
                        int16_t l16_data[160];  /* 160 samples of L16 */
                        int i;
                        
                        switch_buffer_read(tech_pvt->playback_buffer, l16_data, l16_frame_size);
                        
                        /* Convert L16 to PCMU using FreeSWITCH's built-in function */
                        for (i = 0; i < 160; i++) {
                            pcmu_data[i] = linear_to_ulaw(l16_data[i]);
                        }
                    }
                    
                    if (write_codec) {
                        switch_frame_t write_frame = { 0 };
                        write_frame.data = pcmu_data;
                        write_frame.datalen = 160;  /* PCMU: 160 bytes for 160 samples */
                        write_frame.samples = 160;
                        write_frame.rate = 8000;
                        write_frame.codec = write_codec;
                        
                        switch_core_session_write_frame(session, &write_frame, SWITCH_IO_FLAG_NONE, 0);
                    }
                    tech_pvt->underrun_streak = 0;
                } else if (tech_pvt->playback_active && available < frame_size) {
                    /* Underrun - opcionalmente injeta silêncio antes de pausar */
                    tech_pvt->buffer_underruns++;
                    tech_pvt->underrun_streak++;
                    if (tech_pvt->underrun_streak <= tech_pvt->underrun_grace_frames) {
                        /* PCMU silence = 0xFF (μ-law zero) */
                        uint8_t silence_pcmu[160];
                        memset(silence_pcmu, 0xFF, 160);  /* μ-law silence */
                        
                        switch_codec_t *write_codec = switch_core_session_get_write_codec(session);
                        if (write_codec) {
                            switch_frame_t write_frame = { 0 };
                            write_frame.data = silence_pcmu;
                            write_frame.datalen = 160;
                            write_frame.samples = 160;
                            write_frame.rate = 8000;
                            write_frame.codec = write_codec;
                            switch_core_session_write_frame(session, &write_frame, SWITCH_IO_FLAG_NONE, 0);
                        }
                    } else if (available < low_water_mark) {
                        /* Buffer critically low - pause playback to allow refill */
                        tech_pvt->playback_active = 0;
                        tech_pvt->underrun_streak = 0;
                        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), get_stream_log_level(session, SWITCH_LOG_DEBUG),
                            "[BUFFER] low (%zu bytes), pausing to refill\n", available);
                    }
                }
                
                if (available > tech_pvt->buffer_max_used) {
                    tech_pvt->buffer_max_used = available;
                }
                
                switch_mutex_unlock(tech_pvt->playback_mutex);
            }
            
            return stream_frame(bug);
            break;

        case SWITCH_ABC_TYPE_WRITE:
            /* NETPLAY: Audio injection now happens in READ callback via switch_core_session_write_frame */
            break;
        default:
            break;
    }

    return SWITCH_TRUE;
}

static switch_status_t start_capture(switch_core_session_t *session,
                                     switch_media_bug_flag_t flags,
                                     char* wsUri,
                                     int sampling,
                                     int audio_format,
                                     char* metadata)
{
    switch_channel_t *channel = switch_core_session_get_channel(session);
    switch_media_bug_t *bug;
    switch_status_t status;
    switch_codec_t* read_codec;

    void *pUserData = NULL;
    int channels = (flags & SMBF_STEREO) ? 2 : 1;

    if (switch_channel_get_private(channel, MY_BUG_NAME)) {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "mod_audio_stream: bug already attached!\n");
        return SWITCH_STATUS_FALSE;
    }

    if (switch_channel_pre_answer(channel) != SWITCH_STATUS_SUCCESS) {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "mod_audio_stream: channel must have reached pre-answer status before calling start!\n");
        return SWITCH_STATUS_FALSE;
    }

    read_codec = switch_core_session_get_read_codec(session);

    /* Log audio format for debugging - NETPLAY FORK */
    const char* format_name = "L16";
    if (audio_format == 1) format_name = "PCMU (G.711 μ-law)";
    else if (audio_format == 2) format_name = "PCMA (G.711 A-law)";
    
    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_NOTICE, 
        "[NETPLAY] Stream starting: format=%s, sampling=%dHz, channels=%d\n",
        format_name, sampling, channels);

    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "calling stream_session_init.\n");
    if (SWITCH_STATUS_FALSE == stream_session_init(session, responseHandler, read_codec->implementation->actual_samples_per_second,
                                                 wsUri, sampling, channels, audio_format, metadata, &pUserData)) {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "Error initializing mod_audio_stream session.\n");
        return SWITCH_STATUS_FALSE;
    }
    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "adding bug.\n");
    if ((status = switch_core_media_bug_add(session, MY_BUG_NAME, NULL, capture_callback, pUserData, 0, flags, &bug)) != SWITCH_STATUS_SUCCESS) {
        return status;
    }
    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "setting bug private data.\n");
    switch_channel_set_private(channel, MY_BUG_NAME, bug);

    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "exiting start_capture.\n");
    return SWITCH_STATUS_SUCCESS;
}

static switch_status_t do_stop(switch_core_session_t *session, char* text)
{
    switch_status_t status = SWITCH_STATUS_SUCCESS;

    if (text) {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, "mod_audio_stream: stop w/ final text %s\n", text);
    }
    else {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, "mod_audio_stream: stop\n");
    }
    status = stream_session_cleanup(session, text, 0);

    return status;
}

static switch_status_t do_pauseresume(switch_core_session_t *session, int pause)
{
    switch_status_t status = SWITCH_STATUS_SUCCESS;

    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, "mod_audio_stream: %s\n", pause ? "pause" : "resume");
    status = stream_session_pauseresume(session, pause);

    return status;
}

static switch_status_t send_text(switch_core_session_t *session, char* text) {
    switch_status_t status = SWITCH_STATUS_FALSE;
    switch_channel_t *channel = switch_core_session_get_channel(session);
    switch_media_bug_t *bug = switch_channel_get_private(channel, MY_BUG_NAME);

    if (bug) {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_INFO, "mod_audio_stream: sending text: %s.\n", text);
        status = stream_session_send_text(session, text);
    }
    else {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "mod_audio_stream: no bug, failed sending text: %s.\n", text);
    }
    return status;
}

#define STREAM_API_SYNTAX "<uuid> [start | stop | send_text | pause | resume | graceful-shutdown ] [wss-url | path] [mono | mixed | stereo] [8000 | 16000] [l16 | pcmu | pcma] [metadata]"
SWITCH_STANDARD_API(stream_function)
{
    char *mycmd = NULL, *argv[7] = { 0 };
    int argc = 0;

    switch_status_t status = SWITCH_STATUS_FALSE;

    if (!zstr(cmd) && (mycmd = strdup(cmd))) {
        argc = switch_separate_string(mycmd, ' ', argv, (sizeof(argv) / sizeof(argv[0])));
    }
    assert(cmd);
    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_DEBUG, "mod_audio_stream cmd: %s\n", cmd ? cmd : "");

    if (zstr(cmd) || argc < 2 || (0 == strcmp(argv[1], "start") && argc < 4)) {
        switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "Error with command %s %s %s.\n", cmd, argv[0], argv[1]);
        stream->write_function(stream, "-USAGE: %s\n", STREAM_API_SYNTAX);
        goto done;
    } else {
        switch_core_session_t *lsession = NULL;
        if ((lsession = switch_core_session_locate(argv[0]))) {
            if (!strcasecmp(argv[1], "stop")) {
                if(argc > 2 && (is_valid_utf8(argv[2]) != SWITCH_STATUS_SUCCESS)) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "%s contains invalid utf8 characters\n", argv[2]);
                    switch_core_session_rwunlock(lsession);
                    goto done;
                }
                status = do_stop(lsession, argc > 2 ? argv[2] : NULL);
            } else if (!strcasecmp(argv[1], "pause")) {
                status = do_pauseresume(lsession, 1);
            } else if (!strcasecmp(argv[1], "resume")) {
                status = do_pauseresume(lsession, 0);
            } else if (!strcasecmp(argv[1], "send_text")) {
                if (argc < 3) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "send_text requires an argument specifying text to send\n");
                    switch_core_session_rwunlock(lsession);
                    goto done;
                }
                if(is_valid_utf8(argv[2]) != SWITCH_STATUS_SUCCESS) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "%s contains invalid utf8 characters\n", argv[2]);
                    switch_core_session_rwunlock(lsession);
                    goto done;
                }
                status = send_text(lsession, argv[2]);
            } else if (!strcasecmp(argv[1], "start")) {
                //switch_channel_t *channel = switch_core_session_get_channel(lsession);
                char wsUri[MAX_WS_URI];
                int sampling = 8000;
                int audio_format = AUDIO_FORMAT_L16;
                /* NETPLAY v2.5: Full-duplex with Python AEC
                 * - SMBF_READ_STREAM: captures mic audio (may contain echo)
                 * - SMBF_WRITE_REPLACE: needed for streaming playback injection
                 * 
                 * Echo cancellation is done in Python using Speex DSP.
                 * The Python service has both mic and speaker reference for proper AEC.
                 */
                switch_media_bug_flag_t flags = SMBF_READ_STREAM | SMBF_WRITE_REPLACE;
                char *metadata = NULL;
                
                /* Parse format parameter (argv[5]) and metadata (argv[6]) */
                if (argc > 5) {
                    if (0 == strcasecmp(argv[5], "pcmu") || 0 == strcasecmp(argv[5], "ulaw") || 0 == strcasecmp(argv[5], "mulaw")) {
                        audio_format = AUDIO_FORMAT_PCMU;
                        metadata = argc > 6 ? argv[6] : NULL;
                    } else if (0 == strcasecmp(argv[5], "pcma") || 0 == strcasecmp(argv[5], "alaw")) {
                        audio_format = AUDIO_FORMAT_PCMA;
                        metadata = argc > 6 ? argv[6] : NULL;
                    } else if (0 == strcasecmp(argv[5], "l16") || 0 == strcasecmp(argv[5], "linear") || 0 == strcasecmp(argv[5], "pcm")) {
                        audio_format = AUDIO_FORMAT_L16;
                        metadata = argc > 6 ? argv[6] : NULL;
                    } else {
                        /* argv[5] is metadata (backward compatible) */
                        metadata = argv[5];
                    }
                }
                
                if(metadata && (is_valid_utf8(metadata) != SWITCH_STATUS_SUCCESS)) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "%s contains invalid utf8 characters\n", metadata);
                    switch_core_session_rwunlock(lsession);
                    goto done;
                }
                if (0 == strcmp(argv[3], "mixed")) {
                    flags |= SMBF_WRITE_STREAM;
                } else if (0 == strcmp(argv[3], "stereo")) {
                    flags |= SMBF_WRITE_STREAM;
                    flags |= SMBF_STEREO;
                } else if (0 != strcmp(argv[3], "mono")) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "invalid mix type: %s, must be mono, mixed, or stereo\n", argv[3]);
                    switch_core_session_rwunlock(lsession);
                    goto done;
                }
                if (argc > 4) {
                    if (0 == strcmp(argv[4], "16k")) {
                        sampling = 16000;
                    } else if (0 == strcmp(argv[4], "8k")) {
                        sampling = 8000;
                    } else {
                        sampling = atoi(argv[4]);
                    }
                }
                if (!validate_ws_uri(argv[2], &wsUri[0])) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "invalid websocket uri: %s\n", argv[2]);
                } else if (sampling % 8000 != 0) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "invalid sample rate: %s\n", argv[4]);
                } else if (audio_format != AUDIO_FORMAT_L16 && sampling != 8000) {
                    switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                      "G.711 (pcmu/pcma) only supports 8000 Hz sample rate\n");
                } else {
                    status = start_capture(lsession, flags, wsUri, sampling, audio_format, metadata);
                }
            } else {
                switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR,
                                  "unsupported mod_audio_stream cmd: %s\n", argv[1]);
            }
            switch_core_session_rwunlock(lsession);
        } else {
            switch_log_printf(SWITCH_CHANNEL_SESSION_LOG(session), SWITCH_LOG_ERROR, "Error locating session %s\n",
                              argv[0]);
        }
    }

    if (status == SWITCH_STATUS_SUCCESS) {
        stream->write_function(stream, "+OK Success\n");
    } else {
        stream->write_function(stream, "-ERR Operation Failed\n");
    }

done:
    switch_safe_free(mycmd);
    return SWITCH_STATUS_SUCCESS;
}

/* ========================================
 * NETPLAY FORK - G.711 Native + Streaming Playback
 * Version: 2.1.0-netplay
 * Build: 2026-01-19
 * Features:
 *   - Native PCMU/PCMA encoding for WebSocket
 *   - TRUE STREAMING: audio injected directly into channel
 *   - Ring buffer with warmup (100ms) for smooth playback
 *   - Buffer overrun protection (discards old data)
 *   - SMBF_WRITE_REPLACE for frame injection
 *   - Barge-in support via stopAudio command
 * ======================================== */
#define MOD_AUDIO_STREAM_VERSION "2.7.0-netplay"
#define MOD_AUDIO_STREAM_BUILD_DATE "2026-01-25"

SWITCH_MODULE_LOAD_FUNCTION(mod_audio_stream_load)
{
    switch_api_interface_t *api_interface;

    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, 
        "========================================\n");
    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, 
        "mod_audio_stream NETPLAY FORK v%s\n", MOD_AUDIO_STREAM_VERSION);
    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, 
        "Build: %s\n", MOD_AUDIO_STREAM_BUILD_DATE);
    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, 
        "G.711 Native: ENABLED | Streaming Playback: ENABLED\n");
    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, 
        "========================================\n");
    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, "mod_audio_stream API loading..\n");

    /* connect my internal structure to the blank pointer passed to me */
    *module_interface = switch_loadable_module_create_module_interface(pool, modname);

    /* create/register custom event message types */
    if (switch_event_reserve_subclass(EVENT_JSON) != SWITCH_STATUS_SUCCESS ||
        switch_event_reserve_subclass(EVENT_CONNECT) != SWITCH_STATUS_SUCCESS ||
        switch_event_reserve_subclass(EVENT_ERROR) != SWITCH_STATUS_SUCCESS ||
        switch_event_reserve_subclass(EVENT_DISCONNECT) != SWITCH_STATUS_SUCCESS) {
        switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_ERROR, "Couldn't register an event subclass for mod_audio_stream API.\n");
        return SWITCH_STATUS_TERM;
    }
    SWITCH_ADD_API(api_interface, "uuid_audio_stream", "audio_stream API", stream_function, STREAM_API_SYNTAX);
    switch_console_set_complete("add uuid_audio_stream ::console::list_uuid start wss-url metadata");
    switch_console_set_complete("add uuid_audio_stream ::console::list_uuid start wss-url");
    switch_console_set_complete("add uuid_audio_stream ::console::list_uuid stop");
    switch_console_set_complete("add uuid_audio_stream ::console::list_uuid pause");
    switch_console_set_complete("add uuid_audio_stream ::console::list_uuid resume");
    switch_console_set_complete("add uuid_audio_stream ::console::list_uuid send_text");

    switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_NOTICE, "mod_audio_stream API successfully loaded\n");

    /* indicate that the module should continue to be loaded */
    return SWITCH_STATUS_SUCCESS;
}

/*
  Called when the system shuts down
  Macro expands to: switch_status_t mod_audio_stream_shutdown() */
SWITCH_MODULE_SHUTDOWN_FUNCTION(mod_audio_stream_shutdown)
{
    switch_event_free_subclass(EVENT_JSON);
    switch_event_free_subclass(EVENT_CONNECT);
    switch_event_free_subclass(EVENT_DISCONNECT);
    switch_event_free_subclass(EVENT_ERROR);

    return SWITCH_STATUS_SUCCESS;
}
