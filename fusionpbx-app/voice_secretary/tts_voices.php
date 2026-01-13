<?php
/*
    Voice Secretary - TTS Voices Proxy

    Retorna lista de vozes do provider TTS selecionado.
    Faz proxy server-side para o voice-ai-service (evita CORS).

    ⚠️ MULTI-TENANT: usa domain_uuid da sessão e filtra provider por domain_uuid.
*/

require_once dirname(__DIR__, 2) . "/resources/require.php";
require_once "resources/check_auth.php";

header("Content-Type: application/json; charset=utf-8");

if (!permission_exists('voice_secretary_edit') && !permission_exists('voice_secretary_view')) {
    http_response_code(403);
    echo json_encode(["success" => false, "message" => "access denied"]);
    exit;
}

$domain_uuid = $_SESSION['domain_uuid'] ?? null;
if (!$domain_uuid) {
    http_response_code(400);
    echo json_encode(["success" => false, "message" => "domain_uuid not found in session"]);
    exit;
}

$provider_uuid = $_GET['provider_uuid'] ?? '';
$language = $_GET['language'] ?? 'pt-BR';

if (!is_uuid($provider_uuid)) {
    http_response_code(400);
    echo json_encode(["success" => false, "message" => "invalid provider_uuid"]);
    exit;
}

$database = new database;

// Buscar provider_name (multi-tenant)
$sql = "SELECT provider_name
        FROM v_voice_ai_providers
        WHERE domain_uuid = :domain_uuid
          AND voice_ai_provider_uuid = :provider_uuid
          AND provider_type = 'tts'
          AND is_enabled = true
        LIMIT 1";
$params = [
    'domain_uuid' => $domain_uuid,
    'provider_uuid' => $provider_uuid
];
$row = $database->select($sql, $params, 'row');

if (empty($row) || empty($row['provider_name'])) {
    http_response_code(404);
    echo json_encode(["success" => false, "message" => "TTS provider not found for this domain"]);
    exit;
}

$provider_name = $row['provider_name'];

// Buscar service_url (domain settings) - fallback default
$service_url = 'http://127.0.0.1:8089/api/v1';
try {
    $sql = "SELECT setting_value
            FROM v_default_settings
            WHERE domain_uuid = :domain_uuid
              AND default_setting_category = 'voice_secretary'
              AND setting_name = 'service_url'
            LIMIT 1";
    $row = $database->select($sql, ['domain_uuid' => $domain_uuid], 'row');
    if (!empty($row) && !empty($row['setting_value'])) {
        $service_url = $row['setting_value'];
    }
} catch (Exception $e) {
    // ignore
}

$service_url = rtrim($service_url, '/');
$url = $service_url . "/tts/voices"
    . "?domain_uuid=" . urlencode($domain_uuid)
    . "&provider=" . urlencode($provider_name)
    . "&language=" . urlencode($language);

// Proxy request via cURL
$ch = curl_init($url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
curl_setopt($ch, CURLOPT_TIMEOUT, 10);
curl_setopt($ch, CURLOPT_HTTPHEADER, ["Accept: application/json"]);

$resp = curl_exec($ch);
$http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curl_err = curl_error($ch);
curl_close($ch);

if ($resp === false || !empty($curl_err)) {
    http_response_code(502);
    echo json_encode(["success" => false, "message" => "voice-ai-service unreachable", "detail" => $curl_err]);
    exit;
}

if ($http_code < 200 || $http_code >= 300) {
    http_response_code($http_code);
    echo json_encode(["success" => false, "message" => "voice-ai-service error", "detail" => $resp]);
    exit;
}

// Return as-is, but wrap in success for UI convenience
$data = json_decode($resp, true);
if ($data === null) {
    // Not JSON
    echo json_encode(["success" => false, "message" => "invalid response from voice-ai-service", "detail" => $resp]);
    exit;
}

echo json_encode(["success" => true, "voices" => $data]);
exit;

?>
