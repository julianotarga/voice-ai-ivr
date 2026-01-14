<?php
/**
 * Voice AI Provider Class
 * 
 * CRUD operations for AI providers (STT, TTS, LLM, Embeddings).
 * ⚠️ MULTI-TENANT: ALL operations MUST use domain_uuid parameter.
 *
 * @package voice_secretary
 */

class voice_ai_provider {
    
    /**
     * Available provider types
     */
    const TYPES = ['stt', 'tts', 'llm', 'embeddings', 'realtime'];
    
    /**
     * Available providers by type
     */
    const PROVIDERS = [
        'stt' => [
            'whisper_local' => 'Whisper Local (faster-whisper)',
            'whisper_api' => 'OpenAI Whisper API',
            'azure_speech' => 'Azure Speech-to-Text',
            'google_speech' => 'Google Cloud STT',
            'aws_transcribe' => 'AWS Transcribe',
            'deepgram' => 'Deepgram Nova',
        ],
        'tts' => [
            'piper_local' => 'Piper TTS (local)',
            'coqui_local' => 'Coqui TTS (local)',
            'openai_tts' => 'OpenAI TTS',
            'elevenlabs' => 'ElevenLabs',
            'azure_neural' => 'Azure Neural TTS',
            'google_tts' => 'Google Cloud TTS',
            'aws_polly' => 'AWS Polly',
            'playht' => 'Play.ht',
        ],
        'llm' => [
            'openai' => 'OpenAI GPT',
            'azure_openai' => 'Azure OpenAI',
            'anthropic' => 'Anthropic Claude',
            'google_gemini' => 'Google Gemini',
            'aws_bedrock' => 'AWS Bedrock',
            'groq' => 'Groq (Llama)',
            'ollama_local' => 'Ollama (local)',
            'lmstudio_local' => 'LM Studio (local)',
        ],
        'embeddings' => [
            'openai_embeddings' => 'OpenAI Embeddings',
            'azure_embeddings' => 'Azure OpenAI Embeddings',
            'cohere' => 'Cohere Embed',
            'voyage' => 'Voyage AI',
            'local_embeddings' => 'Local (sentence-transformers)',
        ],
        'realtime' => [
            'openai_realtime' => 'OpenAI Realtime API',
            'elevenlabs_conversational' => 'ElevenLabs Conversational',
            'gemini_live' => 'Google Gemini Live',
            'custom_pipeline' => 'Custom Pipeline (Deepgram+Groq+Piper)',
        ],
    ];
    
    /**
     * List all providers for a domain
     */
    public function get_list($domain_uuid, $type = null) {
        $sql = "SELECT * FROM v_voice_ai_providers 
                WHERE domain_uuid = :domain_uuid";
        
        $parameters = [];
        $parameters['domain_uuid'] = $domain_uuid;
        
        if ($type) {
            $sql .= " AND provider_type = :type";
            $parameters['type'] = $type;
        }
        
        $sql .= " ORDER BY provider_type ASC, priority ASC, provider_name ASC";
        
        $database = new database;
        return $database->select($sql, $parameters, 'all');
    }
    
    /**
     * Get single provider by UUID
     */
    public function get($provider_uuid, $domain_uuid = null) {
        $sql = "SELECT * FROM v_voice_ai_providers 
                WHERE voice_ai_provider_uuid = :provider_uuid";
        
        $parameters = [];
        $parameters['provider_uuid'] = $provider_uuid;
        
        if ($domain_uuid) {
            $sql .= " AND domain_uuid = :domain_uuid";
            $parameters['domain_uuid'] = $domain_uuid;
        }
        
        $database = new database;
        return $database->select($sql, $parameters, 'row');
    }
    
    /**
     * Create new provider
     */
    public function create($data, $domain_uuid) {
        $provider_uuid = uuid();
        
        // Validate type
        if (!in_array($data['provider_type'], self::TYPES)) {
            throw new Exception("Invalid provider type: " . $data['provider_type']);
        }
        
        // If setting as default, unset other defaults of same type
        if (!empty($data['is_default'])) {
            $this->unset_defaults($data['provider_type'], $domain_uuid);
        }
        
        $sql = "INSERT INTO v_voice_ai_providers (
            voice_ai_provider_uuid,
            domain_uuid,
            provider_type,
            provider_name,
            config,
            is_enabled,
            is_default,
            priority,
            insert_date
        ) VALUES (
            :provider_uuid,
            :domain_uuid,
            :provider_type,
            :provider_name,
            :config,
            :is_enabled,
            :is_default,
            :priority,
            NOW()
        )";
        
        $parameters = [];
        $parameters['provider_uuid'] = $provider_uuid;
        $parameters['domain_uuid'] = $domain_uuid;
        $parameters['provider_type'] = $data['provider_type'];
        $parameters['provider_name'] = $data['provider_name'];
        $parameters['config'] = is_array($data['config'] ?? null) ? json_encode($data['config']) : ($data['config'] ?? '{}');
        $parameters['is_enabled'] = !empty($data['is_enabled']) ? 'true' : 'false';
        $parameters['is_default'] = !empty($data['is_default']) ? 'true' : 'false';
        $parameters['priority'] = intval($data['priority'] ?? 10);
        
        $database = new database;
        $database->execute($sql, $parameters);
        
        return $provider_uuid;
    }
    
    /**
     * Update existing provider
     */
    public function update($provider_uuid, $data, $domain_uuid) {
        // If changing is_default to true, unset others first
        if (!empty($data['is_default'])) {
            $provider = $this->get($provider_uuid, $domain_uuid);
            if ($provider) {
                $this->unset_defaults($provider['provider_type'], $domain_uuid);
            }
        }
        
        $sql = "UPDATE v_voice_ai_providers SET
            config = :config,
            is_enabled = :is_enabled,
            is_default = :is_default,
            priority = :priority,
            update_date = NOW()
            WHERE voice_ai_provider_uuid = :provider_uuid 
            AND domain_uuid = :domain_uuid";
        
        $parameters = [];
        $parameters['provider_uuid'] = $provider_uuid;
        $parameters['domain_uuid'] = $domain_uuid;
        $parameters['config'] = is_array($data['config'] ?? null) ? json_encode($data['config']) : ($data['config'] ?? '{}');
        $parameters['is_enabled'] = !empty($data['is_enabled']) ? 'true' : 'false';
        $parameters['is_default'] = !empty($data['is_default']) ? 'true' : 'false';
        $parameters['priority'] = intval($data['priority'] ?? 10);
        
        $database = new database;
        $database->execute($sql, $parameters);
        
        return true;
    }
    
    /**
     * Delete provider(s)
     * @param mixed $providers Array with checkbox data from form
     * @param string $domain_uuid Domain UUID for security
     */
    public function delete($providers, $domain_uuid) {
        $database = new database;
        
        foreach ($providers as $provider) {
            if (!empty($provider['checked']) && !empty($provider['uuid']) && is_uuid($provider['uuid'])) {
                $sql = "DELETE FROM v_voice_ai_providers 
                        WHERE voice_ai_provider_uuid = :provider_uuid 
                        AND domain_uuid = :domain_uuid";
                
                $parameters = [];
                $parameters['provider_uuid'] = $provider['uuid'];
                $parameters['domain_uuid'] = $domain_uuid;
                
                $database->execute($sql, $parameters);
            }
        }
        
        message::add($GLOBALS['text']['message-delete'] ?? 'Delete completed.');
        return true;
    }
    
    /**
     * Toggle enabled status
     * @param array $providers Array with checkbox data from form
     * @param string $domain_uuid Domain UUID for security
     */
    public function toggle($providers, $domain_uuid) {
        $database = new database;
        
        foreach ($providers as $provider) {
            if (!empty($provider['checked']) && !empty($provider['uuid']) && is_uuid($provider['uuid'])) {
                // Get current status
                $sql = "SELECT is_enabled FROM v_voice_ai_providers 
                        WHERE voice_ai_provider_uuid = :provider_uuid 
                        AND domain_uuid = :domain_uuid";
                
                $parameters = [];
                $parameters['provider_uuid'] = $provider['uuid'];
                $parameters['domain_uuid'] = $domain_uuid;
                
                $row = $database->select($sql, $parameters, 'row');
                
                if ($row) {
                    // Toggle status
                    $new_status = ($row['is_enabled'] === true || $row['is_enabled'] === 't' || $row['is_enabled'] === 'true') ? 'false' : 'true';
                    
                    $sql = "UPDATE v_voice_ai_providers 
                            SET is_enabled = :is_enabled, update_date = NOW()
                            WHERE voice_ai_provider_uuid = :provider_uuid 
                            AND domain_uuid = :domain_uuid";
                    
                    $parameters = [];
                    $parameters['provider_uuid'] = $provider['uuid'];
                    $parameters['domain_uuid'] = $domain_uuid;
                    $parameters['is_enabled'] = $new_status;
                    
                    $database->execute($sql, $parameters);
                }
            }
        }
        
        message::add($GLOBALS['text']['message-toggle'] ?? 'Toggle completed.');
        return true;
    }
    
    /**
     * Unset all default providers of a type
     */
    private function unset_defaults($type, $domain_uuid) {
        $sql = "UPDATE v_voice_ai_providers 
                SET is_default = 'false' 
                WHERE domain_uuid = :domain_uuid 
                AND provider_type = :type";
        
        $parameters = [];
        $parameters['domain_uuid'] = $domain_uuid;
        $parameters['type'] = $type;
        
        $database = new database;
        $database->execute($sql, $parameters);
    }
    
    /**
     * Get config fields for a provider
     */
    public static function get_config_fields($provider_name) {
        $fields = [
            // STT providers
            'whisper_local' => [
                ['name' => 'model_size', 'label' => 'Model Size', 'type' => 'select', 'options' => ['tiny', 'base', 'small', 'medium', 'large']],
                ['name' => 'device', 'label' => 'Device', 'type' => 'select', 'options' => ['cpu', 'cuda']],
            ],
            'whisper_api' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password', 'required' => true],
            ],
            'azure_speech' => [
                ['name' => 'subscription_key', 'label' => 'Subscription Key', 'type' => 'password', 'required' => true],
                ['name' => 'region', 'label' => 'Region', 'type' => 'text', 'default' => 'brazilsouth'],
            ],
            'google_speech' => [
                ['name' => 'credentials_path', 'label' => 'Credentials JSON Path', 'type' => 'text', 'required' => true],
            ],
            'aws_transcribe' => [
                ['name' => 'aws_access_key_id', 'label' => 'AWS Access Key ID', 'type' => 'password', 'required' => true],
                ['name' => 'aws_secret_access_key', 'label' => 'AWS Secret Access Key', 'type' => 'password', 'required' => true],
                ['name' => 'region_name', 'label' => 'Region', 'type' => 'text', 'default' => 'us-east-1'],
            ],
            'deepgram' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password', 'required' => true],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['nova-2', 'nova', 'enhanced', 'base']],
            ],
            // TTS providers
            'piper_local' => [
                ['name' => 'model_path', 'label' => 'Model Path', 'type' => 'text'],
                ['name' => 'voice', 'label' => 'Voice', 'type' => 'text', 'default' => 'pt_BR-faber-medium'],
            ],
            'coqui_local' => [
                ['name' => 'model_name', 'label' => 'Model Name', 'type' => 'text', 'default' => 'tts_models/multilingual/multi-dataset/xtts_v2'],
            ],
            'openai_tts' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['tts-1', 'tts-1-hd']],
                ['name' => 'voice', 'label' => 'Voice', 'type' => 'select', 'options' => ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']],
            ],
            'elevenlabs' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'voice_id', 'label' => 'Voice ID', 'type' => 'text'],
                ['name' => 'model_id', 'label' => 'Model', 'type' => 'select', 'options' => ['eleven_multilingual_v2', 'eleven_turbo_v2_5', 'eleven_turbo_v2']],
            ],
            'azure_neural' => [
                ['name' => 'subscription_key', 'label' => 'Subscription Key', 'type' => 'password', 'required' => true],
                ['name' => 'region', 'label' => 'Region', 'type' => 'text', 'default' => 'brazilsouth'],
                ['name' => 'voice_name', 'label' => 'Voice Name', 'type' => 'text', 'default' => 'pt-BR-FranciscaNeural'],
            ],
            'google_tts' => [
                ['name' => 'credentials_path', 'label' => 'Credentials JSON Path', 'type' => 'text', 'required' => true],
                ['name' => 'voice_name', 'label' => 'Voice Name', 'type' => 'text', 'default' => 'pt-BR-Standard-A'],
            ],
            'aws_polly' => [
                ['name' => 'aws_access_key_id', 'label' => 'AWS Access Key ID', 'type' => 'password', 'required' => true],
                ['name' => 'aws_secret_access_key', 'label' => 'AWS Secret Access Key', 'type' => 'password', 'required' => true],
                ['name' => 'region_name', 'label' => 'Region', 'type' => 'text', 'default' => 'us-east-1'],
                ['name' => 'voice_id', 'label' => 'Voice ID', 'type' => 'text', 'default' => 'Camila'],
            ],
            'playht' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password', 'required' => true],
                ['name' => 'user_id', 'label' => 'User ID', 'type' => 'text', 'required' => true],
                ['name' => 'voice', 'label' => 'Voice', 'type' => 'text'],
            ],
            // LLM providers
            'openai' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo']],
            ],
            'azure_openai' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password', 'required' => true],
                ['name' => 'endpoint', 'label' => 'Endpoint URL', 'type' => 'text', 'required' => true],
                ['name' => 'deployment_name', 'label' => 'Deployment Name', 'type' => 'text', 'required' => true],
                ['name' => 'api_version', 'label' => 'API Version', 'type' => 'text', 'default' => '2024-02-15-preview'],
            ],
            'anthropic' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['claude-3-5-sonnet-20241022', 'claude-3-opus-20240229', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307']],
            ],
            'google_gemini' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['gemini-2.0-flash-exp', 'gemini-1.5-pro', 'gemini-1.5-flash']],
            ],
            'groq' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768']],
            ],
            'ollama_local' => [
                ['name' => 'base_url', 'label' => 'Ollama URL', 'type' => 'text', 'default' => 'http://localhost:11434'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'text', 'default' => 'llama3'],
            ],
            'lmstudio_local' => [
                ['name' => 'base_url', 'label' => 'LM Studio URL', 'type' => 'text', 'default' => 'http://localhost:1234/v1'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'text'],
            ],
            // Embeddings providers
            'openai_embeddings' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['text-embedding-3-small', 'text-embedding-3-large', 'text-embedding-ada-002']],
            ],
            'azure_embeddings' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password', 'required' => true],
                ['name' => 'endpoint', 'label' => 'Endpoint URL', 'type' => 'text', 'required' => true],
                ['name' => 'deployment_name', 'label' => 'Deployment Name', 'type' => 'text', 'required' => true],
            ],
            'cohere' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['embed-multilingual-v3.0', 'embed-english-v3.0']],
            ],
            'voyage' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'select', 'options' => ['voyage-3', 'voyage-3-lite', 'voyage-multilingual-2']],
            ],
            'local_embeddings' => [
                ['name' => 'model_name', 'label' => 'Model Name', 'type' => 'text', 'default' => 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'],
            ],
            // Realtime providers
            'openai_realtime' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'text', 'default' => 'gpt-4o-realtime-preview'],
                ['name' => 'voice', 'label' => 'Voice', 'type' => 'select', 'options' => ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']],
                ['name' => 'vad_threshold', 'label' => 'VAD Threshold (0-1)', 'type' => 'number', 'step' => '0.05', 'min' => '0', 'max' => '1', 'default' => '0.5'],
                ['name' => 'silence_duration_ms', 'label' => 'Silence Duration (ms)', 'type' => 'number', 'step' => '50', 'min' => '100', 'max' => '3000', 'default' => '500'],
                ['name' => 'prefix_padding_ms', 'label' => 'Prefix Padding (ms)', 'type' => 'number', 'step' => '50', 'min' => '0', 'max' => '1000', 'default' => '300'],
                ['name' => 'max_response_output_tokens', 'label' => 'Max Output Tokens', 'type' => 'number', 'step' => '100', 'min' => '256', 'max' => '8192', 'default' => '4096'],
                ['name' => 'tools_json', 'label' => 'Tools JSON (optional)', 'type' => 'textarea'],
            ],
            'elevenlabs_conversational' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'agent_id', 'label' => 'Agent ID', 'type' => 'text', 'required' => true],
                ['name' => 'voice_id', 'label' => 'Voice ID', 'type' => 'text'],
                ['name' => 'use_agent_config', 'label' => 'Use Agent Config', 'type' => 'select', 'options' => ['true', 'false'], 'default' => 'true'],
                ['name' => 'allow_prompt_override', 'label' => 'Allow Prompt Override', 'type' => 'select', 'options' => ['true', 'false'], 'default' => 'false'],
                ['name' => 'allow_first_message_override', 'label' => 'Allow First Message Override', 'type' => 'select', 'options' => ['true', 'false'], 'default' => 'false'],
                ['name' => 'allow_voice_id_override', 'label' => 'Allow Voice ID Override', 'type' => 'select', 'options' => ['true', 'false'], 'default' => 'false'],
                ['name' => 'language', 'label' => 'Language (ex: pt-BR)', 'type' => 'text'],
                ['name' => 'tts_stability', 'label' => 'TTS Stability (0-1)', 'type' => 'number', 'step' => '0.05', 'min' => '0', 'max' => '1'],
                ['name' => 'tts_speed', 'label' => 'TTS Speed (0.7-1.2)', 'type' => 'number', 'step' => '0.05', 'min' => '0.7', 'max' => '1.2'],
                ['name' => 'tts_similarity_boost', 'label' => 'TTS Similarity Boost (0-1)', 'type' => 'number', 'step' => '0.05', 'min' => '0', 'max' => '1'],
                ['name' => 'custom_llm_extra_body', 'label' => 'Custom LLM Extra Body (JSON)', 'type' => 'textarea'],
                ['name' => 'dynamic_variables', 'label' => 'Dynamic Variables (JSON)', 'type' => 'textarea'],
            ],
            'gemini_live' => [
                ['name' => 'api_key', 'label' => 'API Key', 'type' => 'password'],
                ['name' => 'model', 'label' => 'Model', 'type' => 'text', 'default' => 'gemini-2.0-flash-exp'],
                ['name' => 'voice', 'label' => 'Voice', 'type' => 'select', 'options' => ['Aoede', 'Charon', 'Fenrir', 'Kore', 'Puck']],
                ['name' => 'tools_json', 'label' => 'Tools JSON (optional)', 'type' => 'textarea'],
            ],
            'custom_pipeline' => [
                ['name' => 'stt_provider', 'label' => 'STT Provider', 'type' => 'select', 'options' => ['deepgram', 'whisper_local']],
                ['name' => 'llm_provider', 'label' => 'LLM Provider', 'type' => 'select', 'options' => ['groq', 'ollama']],
                ['name' => 'tts_provider', 'label' => 'TTS Provider', 'type' => 'select', 'options' => ['piper', 'coqui']],
                ['name' => 'deepgram_key', 'label' => 'Deepgram API Key', 'type' => 'password'],
                ['name' => 'groq_key', 'label' => 'Groq API Key', 'type' => 'password'],
            ],
        ];
        
        return $fields[$provider_name] ?? [];
    }
}
?>
