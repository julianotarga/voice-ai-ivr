<?php
/**
 * Voice Secretary Class
 * 
 * CRUD operations for voice AI secretaries.
 * ⚠️ MULTI-TENANT: ALL operations MUST use domain_uuid from session.
 *
 * @package voice_secretary
 */

require_once "domain_validator.php";

class voice_secretary {
    
    private $database;
    private $domain_uuid;
    
    /**
     * Constructor
     */
    public function __construct() {
        $this->database = database::new();
        $this->domain_uuid = domain_validator::require_domain_uuid();
    }
    
    /**
     * List all secretaries for current domain
     */
    public function list($order_by = 'secretary_name', $order = 'asc') {
        $sql = "SELECT * FROM v_voice_secretaries 
                WHERE domain_uuid = :domain_uuid 
                ORDER BY {$order_by} {$order}";
        
        $parameters = [];
        domain_validator::add_to_parameters($parameters);
        
        return $this->database->select($sql, $parameters);
    }
    
    /**
     * Get single secretary by UUID
     */
    public function get($secretary_uuid) {
        $sql = "SELECT * FROM v_voice_secretaries 
                WHERE voice_secretary_uuid = :secretary_uuid 
                AND domain_uuid = :domain_uuid";
        
        $parameters = [
            'secretary_uuid' => $secretary_uuid
        ];
        domain_validator::add_to_parameters($parameters);
        
        $rows = $this->database->select($sql, $parameters);
        return isset($rows[0]) ? $rows[0] : null;
    }
    
    /**
     * Create new secretary
     * 
     * ⚠️ MULTI-TENANT: domain_uuid é adicionado automaticamente
     */
    public function create($data) {
        $secretary_uuid = uuid();
        
        $sql = "INSERT INTO v_voice_secretaries (
            voice_secretary_uuid,
            domain_uuid,
            secretary_name,
            company_name,
            extension,
            processing_mode,
            personality_prompt,
            greeting_message,
            farewell_message,
            stt_provider_uuid,
            tts_provider_uuid,
            llm_provider_uuid,
            embeddings_provider_uuid,
            realtime_provider_uuid,
            tts_voice_id,
            language,
            max_turns,
            transfer_extension,
            is_enabled,
            omniplay_webhook_url,
            insert_date
        ) VALUES (
            :secretary_uuid,
            :domain_uuid,
            :secretary_name,
            :company_name,
            :extension,
            :processing_mode,
            :personality_prompt,
            :greeting_message,
            :farewell_message,
            :stt_provider_uuid,
            :tts_provider_uuid,
            :llm_provider_uuid,
            :embeddings_provider_uuid,
            :realtime_provider_uuid,
            :tts_voice_id,
            :language,
            :max_turns,
            :transfer_extension,
            :is_enabled,
            :omniplay_webhook_url,
            NOW()
        )";
        
        $parameters = [
            'secretary_uuid' => $secretary_uuid,
            'secretary_name' => $data['secretary_name'],
            'company_name' => $data['company_name'] ?? null,
            'extension' => $data['extension'] ?? null,
            'processing_mode' => $data['processing_mode'] ?? 'turn_based',
            'personality_prompt' => $data['system_prompt'] ?? null,
            'greeting_message' => $data['greeting_message'] ?? 'Olá! Como posso ajudar?',
            'farewell_message' => $data['farewell_message'] ?? 'Foi um prazer ajudar! Até logo!',
            'stt_provider_uuid' => $data['stt_provider_uuid'] ?: null,
            'tts_provider_uuid' => $data['tts_provider_uuid'] ?: null,
            'llm_provider_uuid' => $data['llm_provider_uuid'] ?: null,
            'embeddings_provider_uuid' => $data['embeddings_provider_uuid'] ?: null,
            'realtime_provider_uuid' => $data['realtime_provider_uuid'] ?: null,
            'tts_voice_id' => $data['tts_voice'] ?? null,
            'language' => $data['language'] ?? 'pt-BR',
            'max_turns' => $data['max_turns'] ?? 20,
            'transfer_extension' => $data['transfer_extension'] ?? '200',
            'is_enabled' => $data['is_active'] ?? true,
            'omniplay_webhook_url' => $data['webhook_url'] ?? null,
        ];
        domain_validator::add_to_parameters($parameters);
        
        $this->database->execute($sql, $parameters);
        
        return $secretary_uuid;
    }
    
    /**
     * Update existing secretary
     * 
     * ⚠️ MULTI-TENANT: Sempre filtra por domain_uuid
     */
    public function update($secretary_uuid, $data) {
        // Mapeamento: campo do form -> coluna do banco
        $field_mapping = [
            'secretary_name' => 'secretary_name',
            'company_name' => 'company_name',
            'extension' => 'extension',
            'processing_mode' => 'processing_mode',
            'system_prompt' => 'personality_prompt',
            'greeting_message' => 'greeting_message',
            'farewell_message' => 'farewell_message',
            'stt_provider_uuid' => 'stt_provider_uuid',
            'tts_provider_uuid' => 'tts_provider_uuid',
            'llm_provider_uuid' => 'llm_provider_uuid',
            'embeddings_provider_uuid' => 'embeddings_provider_uuid',
            'realtime_provider_uuid' => 'realtime_provider_uuid',
            'tts_voice' => 'tts_voice_id',
            'language' => 'language',
            'max_turns' => 'max_turns',
            'transfer_extension' => 'transfer_extension',
            'is_active' => 'is_enabled',
            'webhook_url' => 'omniplay_webhook_url',
        ];
        
        $set_parts = [];
        $parameters = [
            'secretary_uuid' => $secretary_uuid
        ];
        domain_validator::add_to_parameters($parameters);
        
        foreach ($field_mapping as $form_field => $db_column) {
            if (array_key_exists($form_field, $data)) {
                $value = $data[$form_field];
                // Converter strings vazias para null em UUIDs
                if (strpos($db_column, '_uuid') !== false && empty($value)) {
                    $value = null;
                }
                $set_parts[] = "{$db_column} = :{$form_field}";
                $parameters[$form_field] = $value;
            }
        }
        
        if (empty($set_parts)) {
            return false;
        }
        
        $set_parts[] = "update_date = NOW()";
        
        $sql = "UPDATE v_voice_secretaries 
                SET " . implode(', ', $set_parts) . "
                WHERE voice_secretary_uuid = :secretary_uuid 
                AND domain_uuid = :domain_uuid";
        
        $this->database->execute($sql, $parameters);
        
        return true;
    }
    
    /**
     * Delete secretary
     */
    public function delete($secretary_uuid) {
        $sql = "DELETE FROM v_voice_secretaries 
                WHERE voice_secretary_uuid = :secretary_uuid 
                AND domain_uuid = :domain_uuid";
        
        $parameters = [
            'secretary_uuid' => $secretary_uuid
        ];
        domain_validator::add_to_parameters($parameters);
        
        $this->database->execute($sql, $parameters);
        
        return true;
    }
    
    /**
     * Get providers for dropdown (by type)
     * 
     * ⚠️ Nomes corretos das colunas conforme migration
     */
    public function get_providers($type) {
        $sql = "SELECT voice_ai_provider_uuid as provider_uuid, provider_name, display_name
                FROM v_voice_ai_providers 
                WHERE domain_uuid = :domain_uuid 
                AND provider_type = :type 
                AND is_enabled = true 
                ORDER BY priority ASC, provider_name ASC";
        
        $parameters = [
            'type' => $type
        ];
        domain_validator::add_to_parameters($parameters);
        
        return $this->database->select($sql, $parameters);
    }
    
    /**
     * Test TTS voice
     */
    public function test_voice($text, $voice_id = null, $provider_uuid = null) {
        $service_url = $_ENV['VOICE_AI_SERVICE_URL'] ?? 'http://127.0.0.1:8100/api/v1';
        
        $payload = json_encode([
            'domain_uuid' => $this->domain_uuid,
            'text' => $text,
            'voice_id' => $voice_id,
        ]);
        
        $ch = curl_init($service_url . '/synthesize');
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
        curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json']);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 30);
        
        $response = curl_exec($ch);
        $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        
        if ($http_code == 200) {
            $data = json_decode($response, true);
            return $data['audio_file'] ?? null;
        }
        
        return null;
    }
}
?>
