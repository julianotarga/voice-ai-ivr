<?php
/**
 * OmniPlay API Client
 * 
 * Permite que o FusionPBX se comunique com o OmniPlay
 * para buscar filas, usuários e outras configurações.
 * 
 * Uso:
 *   $client = new OmniPlayAPIClient($domain_uuid, $pdo);
 *   $queues = $client->getQueues();
 *   $users = $client->getUsers();
 * 
 * Ref: voice-ai-ivr/docs/TRANSFER_SETTINGS_VS_RULES.md
 */

class OmniPlayAPIClient {
    private $domain_uuid;
    private $pdo;
    private $api_url;
    private $api_token;
    private $company_id;
    private $cache_ttl = 300; // 5 minutos
    private $last_error;
    
    /**
     * Construtor
     * 
     * @param string $domain_uuid UUID do domínio FusionPBX
     * @param PDO $pdo Conexão com banco de dados
     */
    public function __construct($domain_uuid, $pdo) {
        $this->domain_uuid = $domain_uuid;
        $this->pdo = $pdo;
        $this->loadSettings();
    }
    
    /**
     * Carrega configurações do banco de dados
     */
    private function loadSettings() {
        $sql = "SELECT omniplay_api_url, omniplay_api_token, omniplay_company_id 
                FROM v_voice_omniplay_settings 
                WHERE domain_uuid = :domain_uuid 
                LIMIT 1";
        
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute([':domain_uuid' => $this->domain_uuid]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        
        if ($row) {
            $this->api_url = rtrim($row['omniplay_api_url'] ?? '', '/');
            $this->api_token = $row['omniplay_api_token'] ?? null;
            $this->company_id = $row['omniplay_company_id'] ?? null;
        }
    }
    
    /**
     * Verifica se a integração está configurada
     * 
     * @return bool
     */
    public function isConfigured() {
        return !empty($this->api_url) && !empty($this->api_token);
    }
    
    /**
     * Retorna o último erro ocorrido
     * 
     * @return string|null
     */
    public function getLastError() {
        return $this->last_error;
    }
    
    /**
     * Testa a conexão com o OmniPlay
     * 
     * @return array|false Dados da empresa ou false em caso de erro
     */
    public function testConnection() {
        if (!$this->isConfigured()) {
            $this->last_error = "Integração não configurada";
            return false;
        }
        
        $response = $this->makeRequest('/api/voice/external/ping');
        
        if ($response && isset($response['success']) && $response['success']) {
            return $response;
        }
        
        return false;
    }
    
    /**
     * Busca filas do OmniPlay
     * 
     * @param bool $use_cache Usar cache local
     * @return array Lista de filas
     */
    public function getQueues($use_cache = true) {
        if (!$this->isConfigured()) {
            return [];
        }
        
        // Verificar cache
        if ($use_cache) {
            $cached = $this->getFromCache('queues');
            if ($cached !== null) {
                return $cached;
            }
        }
        
        $response = $this->makeRequest('/api/voice/external/queues');
        
        if ($response && isset($response['queues'])) {
            $this->saveToCache('queues', $response['queues']);
            return $response['queues'];
        }
        
        return [];
    }
    
    /**
     * Busca usuários do OmniPlay
     * 
     * @param int|null $queue_id Filtrar por fila (opcional)
     * @param bool $use_cache Usar cache local
     * @return array Lista de usuários
     */
    public function getUsers($queue_id = null, $use_cache = true) {
        if (!$this->isConfigured()) {
            return [];
        }
        
        $cache_key = 'users' . ($queue_id ? "_q{$queue_id}" : '');
        
        // Verificar cache
        if ($use_cache) {
            $cached = $this->getFromCache($cache_key);
            if ($cached !== null) {
                return $cached;
            }
        }
        
        $endpoint = '/api/voice/external/users';
        if ($queue_id) {
            $endpoint .= '?queueId=' . urlencode($queue_id);
        }
        
        $response = $this->makeRequest($endpoint);
        
        if ($response && isset($response['users'])) {
            $this->saveToCache($cache_key, $response['users']);
            return $response['users'];
        }
        
        return [];
    }
    
    /**
     * Busca informações da empresa
     * 
     * @return array|null
     */
    public function getCompanyInfo() {
        if (!$this->isConfigured()) {
            return null;
        }
        
        $response = $this->makeRequest('/api/voice/external/company');
        
        if ($response && isset($response['company'])) {
            return $response['company'];
        }
        
        return null;
    }
    
    /**
     * Faz requisição HTTP à API do OmniPlay
     * 
     * @param string $endpoint Endpoint da API
     * @param string $method Método HTTP (GET, POST)
     * @param array $data Dados para POST
     * @return array|null
     */
    private function makeRequest($endpoint, $method = 'GET', $data = null) {
        $url = $this->api_url . $endpoint;
        
        $headers = [
            'Authorization: Bearer ' . $this->api_token,
            'Content-Type: application/json',
            'Accept: application/json'
        ];
        
        $ch = curl_init();
        
        curl_setopt_array($ch, [
            CURLOPT_URL => $url,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_TIMEOUT => 10,
            CURLOPT_SSL_VERIFYPEER => true
        ]);
        
        if ($method === 'POST') {
            curl_setopt($ch, CURLOPT_POST, true);
            if ($data) {
                curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
            }
        }
        
        $response = curl_exec($ch);
        $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        
        curl_close($ch);
        
        if ($error) {
            $this->last_error = "Erro de conexão: {$error}";
            return null;
        }
        
        if ($http_code >= 400) {
            $this->last_error = "HTTP {$http_code}: " . ($response ?: 'Erro desconhecido');
            return null;
        }
        
        $decoded = json_decode($response, true);
        
        if (json_last_error() !== JSON_ERROR_NONE) {
            $this->last_error = "Resposta inválida: " . json_last_error_msg();
            return null;
        }
        
        return $decoded;
    }
    
    /**
     * Busca dados do cache local
     * 
     * @param string $key Chave do cache
     * @return array|null
     */
    private function getFromCache($key) {
        $cache_key = "omniplay_{$this->domain_uuid}_{$key}";
        
        // ✅ FIX: PostgreSQL INTERVAL precisa de aspas simples ao redor do valor
        // Usamos interpolação segura pois $this->cache_ttl é int definido internamente
        $ttl = (int) $this->cache_ttl;  // Garantir que é int
        
        $sql = "SELECT cache_data, cached_at 
                FROM v_voice_omniplay_cache 
                WHERE domain_uuid = :domain_uuid 
                  AND cache_key = :cache_key
                  AND cached_at > NOW() - INTERVAL '{$ttl} seconds'
                LIMIT 1";
        
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute([
            ':domain_uuid' => $this->domain_uuid,
            ':cache_key' => $cache_key
        ]);
        
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        
        if ($row && isset($row['cache_data'])) {
            return json_decode($row['cache_data'], true);
        }
        
        return null;
    }
    
    /**
     * Salva dados no cache local
     * 
     * @param string $key Chave do cache
     * @param array $data Dados para cachear
     */
    private function saveToCache($key, $data) {
        $cache_key = "omniplay_{$this->domain_uuid}_{$key}";
        $cache_data = json_encode($data);
        
        $sql = "INSERT INTO v_voice_omniplay_cache (domain_uuid, cache_key, cache_data, cached_at)
                VALUES (:domain_uuid, :cache_key, :cache_data, NOW())
                ON CONFLICT (domain_uuid, cache_key) 
                DO UPDATE SET cache_data = EXCLUDED.cache_data, cached_at = NOW()";
        
        try {
            $stmt = $this->pdo->prepare($sql);
            $stmt->execute([
                ':domain_uuid' => $this->domain_uuid,
                ':cache_key' => $cache_key,
                ':cache_data' => $cache_data
            ]);
        } catch (PDOException $e) {
            // Cache é opcional, não bloquear em caso de erro
            error_log("OmniPlay cache error: " . $e->getMessage());
        }
    }
    
    /**
     * Limpa cache local
     * 
     * @param string|null $key Chave específica ou null para limpar tudo
     */
    public function clearCache($key = null) {
        if ($key) {
            $cache_key = "omniplay_{$this->domain_uuid}_{$key}";
            $sql = "DELETE FROM v_voice_omniplay_cache WHERE domain_uuid = :domain_uuid AND cache_key = :cache_key";
            $stmt = $this->pdo->prepare($sql);
            $stmt->execute([':domain_uuid' => $this->domain_uuid, ':cache_key' => $cache_key]);
        } else {
            $sql = "DELETE FROM v_voice_omniplay_cache WHERE domain_uuid = :domain_uuid";
            $stmt = $this->pdo->prepare($sql);
            $stmt->execute([':domain_uuid' => $this->domain_uuid]);
        }
    }
    
    /**
     * Força sincronização (limpa cache e rebusca)
     * 
     * @return array ['queues' => [...], 'users' => [...], 'company' => {...}]
     */
    public function forceSync() {
        $this->clearCache();
        
        return [
            'company' => $this->getCompanyInfo(),
            'queues' => $this->getQueues(false),
            'users' => $this->getUsers(null, false)
        ];
    }
}

?>
