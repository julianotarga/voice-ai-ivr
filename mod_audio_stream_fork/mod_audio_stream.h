#ifndef MOD_AUDIO_STREAM_H
#define MOD_AUDIO_STREAM_H

#include <switch.h>
#include <speex/speex_resampler.h>

#define MY_BUG_NAME "audio_stream"
#define MAX_SESSION_ID (256)
#define MAX_WS_URI (4096)
#define MAX_METADATA_LEN (8192)

#define EVENT_CONNECT           "mod_audio_stream::connect"
#define EVENT_DISCONNECT        "mod_audio_stream::disconnect"
#define EVENT_ERROR             "mod_audio_stream::error"
#define EVENT_JSON              "mod_audio_stream::json"
#define EVENT_PLAY              "mod_audio_stream::play"

/* Audio format types */
#define AUDIO_FORMAT_L16    0   /* Linear PCM 16-bit (default) */
#define AUDIO_FORMAT_PCMU   1   /* G.711 µ-law */
#define AUDIO_FORMAT_PCMA   2   /* G.711 A-law */

typedef void (*responseHandler_t)(switch_core_session_t* session, const char* eventName, const char* json);

struct private_data {
    switch_mutex_t *mutex;
    char sessionId[MAX_SESSION_ID];
    SpeexResamplerState *resampler;
    responseHandler_t responseHandler;
    void *pAudioStreamer;
    char ws_uri[MAX_WS_URI];
    int sampling;
    int channels;
    /* Bitfields grouped together for proper alignment */
    int audio_paused:1;
    int close_requested:1;
    int cleanup_started:1;
    int codec_initialized:1;    /* Flag indicating if G.711 codec is initialized */
    int playback_active:1;      /* NETPLAY: Flag indicating playback is active */
    int playback_is_pcmu:1;     /* NETPLAY v2.7: Buffer contains PCMU, skip L16→PCMU conversion */
    char initialMetadata[8192];
    switch_buffer_t *sbuffer;
    switch_buffer_t *playback_buffer;  /* NETPLAY: Buffer for streaming playback */
    switch_mutex_t *playback_mutex;    /* NETPLAY: Mutex for playback buffer */
    int rtp_packets;
    int audio_format;           /* AUDIO_FORMAT_L16, AUDIO_FORMAT_PCMU, AUDIO_FORMAT_PCMA */
    switch_codec_t write_codec; /* Codec for encoding L16 to PCMU/PCMA */
    switch_size_t playback_buflen;       /* Playback buffer size in bytes */
    switch_size_t warmup_threshold;      /* Warmup threshold in bytes */
    switch_size_t low_water_mark;        /* Low water mark in bytes */
    uint64_t first_audio_ts;             /* Timestamp of first streamAudio chunk */
    uint64_t playback_start_ts;          /* Timestamp when playback starts */
    uint32_t buffer_overruns;            /* Buffer overrun counter */
    uint32_t buffer_underruns;           /* Buffer underrun counter */
    switch_size_t buffer_max_used;       /* Max buffered bytes observed */
    uint32_t underrun_streak;            /* Consecutive underrun frames */
    uint32_t underrun_grace_frames;      /* Grace frames before pausing */
    /* NETPLAY v2.8: Audio Chunk Queue for burst-tolerant playback */
    void *audio_chunk_queue;             /* C++ AudioChunkQueue object pointer */
    uint32_t chunk_queue_pulls;          /* Counter for chunks pulled from queue */
};

typedef struct private_data private_t;

/* NETPLAY v2.8: C wrapper functions for AudioChunkQueue (implemented in C++) */
#ifdef __cplusplus
extern "C" {
#endif
void* audio_chunk_queue_create(void);
void audio_chunk_queue_destroy(void* queue);
void audio_chunk_queue_push(void* queue, const uint8_t* data, size_t len);
size_t audio_chunk_queue_pull_to_buffer(void* queue, switch_buffer_t* buffer, size_t max_bytes);
void audio_chunk_queue_clear(void* queue);
size_t audio_chunk_queue_size(void* queue);
size_t audio_chunk_queue_total_bytes(void* queue);
#ifdef __cplusplus
}
#endif

enum notifyEvent_t {
    CONNECT_SUCCESS,
    CONNECT_ERROR,
    CONNECTION_DROPPED,
    MESSAGE
};

#endif //MOD_AUDIO_STREAM_H
