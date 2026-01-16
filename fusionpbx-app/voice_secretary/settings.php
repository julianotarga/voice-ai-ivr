<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Settings
	Global settings for voice AI.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
	
	📋 Valores padrão sincronizados com voice-ai-service/config/settings.py
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";

//check permissions
	if (permission_exists('voice_secretary_edit')) {
		//access granted
	}
	else {
		echo "access denied";
		exit;
	}

//add multi-lingual support
	$language = new text;
	$text = $language->get();

//get domain_uuid from session
	$domain_uuid = $_SESSION['domain_uuid'] ?? null;
	if (!$domain_uuid) {
		echo "access denied";
		exit;
	}

//ensure settings table exists
	$database = new database;
	$sql_create = "CREATE TABLE IF NOT EXISTS v_voice_secretary_settings (
		voice_secretary_setting_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
		domain_uuid UUID NOT NULL,
		setting_name VARCHAR(100) NOT NULL,
		setting_value TEXT,
		insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
		update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
		UNIQUE(domain_uuid, setting_name)
	)";
	$database->execute($sql_create, []);

//====================================================================
// 📌 DEFAULT VALUES - Synchronized with Python settings.py
//====================================================================
$defaults = [
	// Service Configuration
	// NOTA: URL base SEM /api/v1 - o prefixo é adicionado pelos endpoints
	'service_url' => 'http://127.0.0.1:8100',
	'max_concurrent_calls' => 10,
	'default_max_turns' => 20,
	'rate_limit_rpm' => 60,
	
	// ESL Configuration (FreeSWITCH)
	'esl_host' => '127.0.0.1',
	'esl_port' => 8021,
	'esl_password' => 'ClueCon',
	'esl_connect_timeout' => 5.0,
	'esl_read_timeout' => 30.0,
	
	// Transfer Settings
	'transfer_default_timeout' => 30,
	'transfer_announce_enabled' => 'true',
	'transfer_music_on_hold' => 'local_stream://moh',
	'transfer_cache_ttl_seconds' => 300,
	
	// Callback Settings
	'callback_enabled' => 'true',
	'callback_expiration_hours' => 24,
	'callback_max_notifications' => 5,
	'callback_min_interval_minutes' => 10,
	
	// OmniPlay Integration
	'omniplay_api_url' => 'http://127.0.0.1:8080',
	'omniplay_api_timeout_ms' => 10000,
	'omniplay_api_key' => '',
	'omniplay_webhook_url' => '',
	
	// Data Management
	'data_retention_days' => 90,
	'recording_enabled' => 'true',
	
	// Audio Settings
	'audio_sample_rate' => 16000,
	'silence_threshold_ms' => 3000,
	'max_recording_seconds' => 30,
];

//get current settings
	$sql = "SELECT setting_name, setting_value FROM v_voice_secretary_settings ";
	$sql .= "WHERE domain_uuid = :domain_uuid";
	$parameters['domain_uuid'] = $domain_uuid;
	$rows = $database->select($sql, $parameters, 'all') ?: [];
	unset($sql, $parameters);

	$settings = [];
	foreach ($rows as $row) {
		$settings[$row['setting_name']] = $row['setting_value'];
	}

//merge with defaults - saved values override defaults
	$merged = array_merge($defaults, array_filter($settings, fn($v) => $v !== null && $v !== ''));

//process form submission
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {

		//validate the token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: settings.php');
			exit;
		}

		$new_settings = [
			// Service
			'service_url' => trim($_POST['service_url'] ?? $defaults['service_url']),
			'max_concurrent_calls' => intval($_POST['max_concurrent_calls'] ?? $defaults['max_concurrent_calls']),
			'default_max_turns' => intval($_POST['default_max_turns'] ?? $defaults['default_max_turns']),
			'rate_limit_rpm' => intval($_POST['rate_limit_rpm'] ?? $defaults['rate_limit_rpm']),
			
			// ESL
			'esl_host' => trim($_POST['esl_host'] ?? $defaults['esl_host']),
			'esl_port' => intval($_POST['esl_port'] ?? $defaults['esl_port']),
			'esl_password' => $_POST['esl_password'] ?? $defaults['esl_password'],
			'esl_connect_timeout' => floatval($_POST['esl_connect_timeout'] ?? $defaults['esl_connect_timeout']),
			'esl_read_timeout' => floatval($_POST['esl_read_timeout'] ?? $defaults['esl_read_timeout']),
			
			// Transfer
			'transfer_default_timeout' => intval($_POST['transfer_default_timeout'] ?? $defaults['transfer_default_timeout']),
			'transfer_announce_enabled' => isset($_POST['transfer_announce_enabled']) ? 'true' : 'false',
			'transfer_music_on_hold' => trim($_POST['transfer_music_on_hold'] ?? $defaults['transfer_music_on_hold']),
			'transfer_cache_ttl_seconds' => intval($_POST['transfer_cache_ttl_seconds'] ?? $defaults['transfer_cache_ttl_seconds']),
			
			// Callback
			'callback_enabled' => isset($_POST['callback_enabled']) ? 'true' : 'false',
			'callback_expiration_hours' => intval($_POST['callback_expiration_hours'] ?? $defaults['callback_expiration_hours']),
			'callback_max_notifications' => intval($_POST['callback_max_notifications'] ?? $defaults['callback_max_notifications']),
			'callback_min_interval_minutes' => intval($_POST['callback_min_interval_minutes'] ?? $defaults['callback_min_interval_minutes']),
			
			// OmniPlay
			'omniplay_api_url' => trim($_POST['omniplay_api_url'] ?? $defaults['omniplay_api_url']),
			'omniplay_api_timeout_ms' => intval($_POST['omniplay_api_timeout_ms'] ?? $defaults['omniplay_api_timeout_ms']),
			'omniplay_api_key' => $_POST['omniplay_api_key'] ?? '',
			'omniplay_webhook_url' => trim($_POST['omniplay_webhook_url'] ?? ''),
			
			// Data
			'data_retention_days' => intval($_POST['data_retention_days'] ?? $defaults['data_retention_days']),
			'recording_enabled' => isset($_POST['recording_enabled']) ? 'true' : 'false',
			
			// Audio
			'audio_sample_rate' => intval($_POST['audio_sample_rate'] ?? $defaults['audio_sample_rate']),
			'silence_threshold_ms' => intval($_POST['silence_threshold_ms'] ?? $defaults['silence_threshold_ms']),
			'max_recording_seconds' => intval($_POST['max_recording_seconds'] ?? $defaults['max_recording_seconds']),
		];
		
		foreach ($new_settings as $name => $value) {
			$sql_upsert = "INSERT INTO v_voice_secretary_settings (domain_uuid, setting_name, setting_value, update_date) ";
			$sql_upsert .= "VALUES (:domain_uuid, :name, :value, NOW()) ";
			$sql_upsert .= "ON CONFLICT (domain_uuid, setting_name) DO UPDATE SET setting_value = :value, update_date = NOW()";
			
			$database->execute($sql_upsert, [
				'domain_uuid' => $domain_uuid,
				'name' => $name,
				'value' => (string)$value
			]);
		}
		
		// 🔄 CRITICAL: Notify Voice AI Service to reload settings cache
		$service_url = $new_settings['service_url'] ?? $defaults['service_url'];
		// Endpoint: /api/v1/callback/settings/reload
		$reload_url = rtrim($service_url, '/') . '/api/v1/callback/settings/reload';
		
		$ch = curl_init();
		curl_setopt($ch, CURLOPT_URL, $reload_url);
		curl_setopt($ch, CURLOPT_POST, true);
		curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query(['domain_uuid' => $domain_uuid]));
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
		curl_setopt($ch, CURLOPT_TIMEOUT, 5);
		curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
		curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/x-www-form-urlencoded']);
		$reload_response = curl_exec($ch);
		$reload_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		$curl_error = curl_error($ch);
		curl_close($ch);
		
		if ($reload_code >= 200 && $reload_code < 300) {
			message::add($text['message-update'] ?? 'Settings saved and cache reloaded.');
		} else {
			message::add($text['message-update'] ?? 'Settings saved.');
			if ($curl_error || $reload_code > 0) {
				message::add('⚠️ Could not reload Voice AI cache: ' . ($curl_error ?: "HTTP $reload_code") . '. Changes will apply on next restart.', 'alert');
			}
		}
		
		header('Location: settings.php');
		exit;
	}

//handle test connections
	$test_result = null;
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['test_esl'])) {
		$esl_host = trim($_POST['esl_host'] ?? $merged['esl_host']);
		$esl_port = intval($_POST['esl_port'] ?? $merged['esl_port']);
		$esl_password = $_POST['esl_password'] ?? $merged['esl_password'];
		
		// Test ESL connection
		$fp = @fsockopen($esl_host, $esl_port, $errno, $errstr, 3);
		if ($fp) {
			fclose($fp);
			$test_result = ['type' => 'positive', 'message' => "✅ Conexão ESL bem-sucedida! ($esl_host:$esl_port)"];
		} else {
			$test_result = ['type' => 'negative', 'message' => "❌ Falha na conexão ESL: $errstr ($errno)"];
		}
	}
	
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['test_omniplay'])) {
		$omniplay_url = trim($_POST['omniplay_api_url'] ?? $merged['omniplay_api_url']);
		
		// Test OmniPlay connection
		$ch = curl_init($omniplay_url . '/health');
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
		curl_setopt($ch, CURLOPT_TIMEOUT, 5);
		curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
		$response = curl_exec($ch);
		$http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		$curl_error = curl_error($ch);
		curl_close($ch);
		
		if ($http_code >= 200 && $http_code < 400) {
			$test_result = ['type' => 'positive', 'message' => "✅ Conexão OmniPlay bem-sucedida! (HTTP $http_code)"];
		} else {
			$test_result = ['type' => 'negative', 'message' => "❌ Falha na conexão OmniPlay: " . ($curl_error ?: "HTTP $http_code")];
		}
	}
	
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['test_service'])) {
		$service_url = trim($_POST['service_url'] ?? $merged['service_url']);
		
		// Test Voice AI Service
		$ch = curl_init($service_url . '/health');
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
		curl_setopt($ch, CURLOPT_TIMEOUT, 5);
		curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
		$response = curl_exec($ch);
		$http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		$curl_error = curl_error($ch);
		curl_close($ch);
		
		if ($http_code >= 200 && $http_code < 400) {
			$test_result = ['type' => 'positive', 'message' => "✅ Voice AI Service respondendo! (HTTP $http_code)"];
		} else {
			$test_result = ['type' => 'negative', 'message' => "❌ Voice AI Service não responde: " . ($curl_error ?: "HTTP $http_code")];
		}
	}

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = $text['title-settings'] ?? 'Settings';
	require_once "resources/header.php";

//include tab navigation
	$current_page = 'settings';
	require_once "resources/nav_tabs.php";

//show test result message
	if ($test_result) {
		message::add($test_result['message'], $test_result['type']);
	}

//helper function for checkbox checked state
function is_checked($value) {
	return ($value === 'true' || $value === true || $value === '1' || $value === 1) ? 'checked' : '';
}

//show the content
	echo "<form method='post' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".($text['title-settings'] ?? 'Settings')."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'submit','name'=>'submit','label'=>$text['button-save'],'icon'=>$_SESSION['theme']['button_icon_save'],'id'=>'btn_save']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo "<div class='card' style='padding: 15px; margin-bottom: 15px; background: #e8f5e9; border-left: 4px solid #4caf50;'>\n";
	echo "	<b>💡 Dica:</b> Os valores padrão já estão preenchidos e são recomendados para a maioria das instalações. ";
	echo "	Altere apenas se necessário para o seu ambiente específico.\n";
	echo "</div>\n";

	echo "<div class='card'>\n";
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	//====================================================================
	// 🔧 SERVICE CONFIGURATION
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #1976d2; color: white; font-weight: bold; padding: 12px;'>".($text['header-service'] ?? '🔧 Service Configuration')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-service_url'] ?? 'Voice AI Service URL')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<div style='display: flex; gap: 10px; align-items: center;'>\n";
	echo "			<input class='formfld' type='url' name='service_url' value='".escape($merged['service_url'])."' style='flex: 1;' placeholder='".$defaults['service_url']."'>\n";
	echo button::create(['type'=>'submit','name'=>'test_service','label'=>'Testar','icon'=>'fa-plug','style'=>'padding: 5px 10px;']);
	echo "		</div>\n";
	echo "		<br /><span class='vtable-hint'>URL base do serviço Voice AI, SEM /api/v1 (ex: http://127.0.0.1:8100). <b>Padrão:</b> ".$defaults['service_url']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-max_concurrent'] ?? 'Max Concurrent Calls')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='max_concurrent_calls' min='1' max='100' value='".intval($merged['max_concurrent_calls'])."' style='width: 100px;'>\n";
	echo "		<span style='margin-left: 5px;'>chamadas simultâneas</span>\n";
	echo "		<br /><span class='vtable-hint'>Limite máximo de chamadas ativas. <b>Padrão:</b> ".$defaults['max_concurrent_calls']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-default_max_turns'] ?? 'Default Max Turns')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='default_max_turns' min='1' max='100' value='".intval($merged['default_max_turns'])."' style='width: 100px;'>\n";
	echo "		<span style='margin-left: 5px;'>turnos de conversa</span>\n";
	echo "		<br /><span class='vtable-hint'>Máximo de interações antes de oferecer transferência. <b>Padrão:</b> ".$defaults['default_max_turns']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-rate_limit'] ?? 'Rate Limit (RPM)')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='rate_limit_rpm' min='1' max='1000' value='".intval($merged['rate_limit_rpm'])."' style='width: 100px;'>\n";
	echo "		<span style='margin-left: 5px;'>requisições por minuto</span>\n";
	echo "		<br /><span class='vtable-hint'>Limite de requisições por minuto à API. <b>Padrão:</b> ".$defaults['rate_limit_rpm']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//====================================================================
	// 📞 ESL CONFIGURATION (FreeSWITCH)
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #e65100; color: white; font-weight: bold; padding: 12px;'>".($text['header-esl'] ?? '📞 ESL Configuration (FreeSWITCH)')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>ESL Host</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='esl_host' value='".escape($merged['esl_host'])."' style='width: 200px;' placeholder='".$defaults['esl_host']."'>\n";
	echo "		<br /><span class='vtable-hint'>IP do servidor FreeSWITCH. <b>Padrão:</b> ".$defaults['esl_host']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>ESL Port</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<div style='display: flex; gap: 10px; align-items: center;'>\n";
	echo "			<input class='formfld' type='number' name='esl_port' min='1' max='65535' value='".intval($merged['esl_port'])."' style='width: 100px;'>\n";
	echo button::create(['type'=>'submit','name'=>'test_esl','label'=>'Testar Conexão','icon'=>'fa-plug','style'=>'padding: 5px 10px;']);
	echo "		</div>\n";
	echo "		<br /><span class='vtable-hint'>Porta do Event Socket. <b>Padrão:</b> ".$defaults['esl_port']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>ESL Password</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='password' name='esl_password' value='".escape($merged['esl_password'])."' style='width: 200px;' autocomplete='new-password'>\n";
	echo "		<br /><span class='vtable-hint'>Senha do ESL (event_socket.conf.xml). <b>Padrão:</b> ".$defaults['esl_password']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>ESL Timeouts</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<div style='display: flex; gap: 10px; align-items: center;'>\n";
	echo "			<label>Connect:</label>\n";
	echo "			<input class='formfld' type='number' name='esl_connect_timeout' min='1' max='30' step='0.5' value='".floatval($merged['esl_connect_timeout'])."' style='width: 70px;'>s\n";
	echo "			<label style='margin-left: 15px;'>Read:</label>\n";
	echo "			<input class='formfld' type='number' name='esl_read_timeout' min='5' max='120' step='1' value='".floatval($merged['esl_read_timeout'])."' style='width: 70px;'>s\n";
	echo "		</div>\n";
	echo "		<br /><span class='vtable-hint'>Timeouts de conexão e leitura do ESL. <b>Padrão:</b> ".$defaults['esl_connect_timeout']."s / ".$defaults['esl_read_timeout']."s</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//====================================================================
	// 🔀 TRANSFER SETTINGS
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #7b1fa2; color: white; font-weight: bold; padding: 12px;'>".($text['header-transfer'] ?? '🔀 Transfer Settings')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Transfer Timeout</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='transfer_default_timeout' min='5' max='120' value='".intval($merged['transfer_default_timeout'])."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>segundos</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo máximo para aguardar atendimento antes de fallback. <b>Padrão:</b> ".$defaults['transfer_default_timeout']."s</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Announce Transfer</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input type='checkbox' name='transfer_announce_enabled' id='transfer_announce_enabled' ".is_checked($merged['transfer_announce_enabled']).">\n";
	echo "		<label for='transfer_announce_enabled'>Anunciar transferência ao caller</label>\n";
	echo "		<br /><span class='vtable-hint'>Informar ao cliente que está sendo transferido. <b>Padrão:</b> Habilitado</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Music on Hold</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='transfer_music_on_hold' value='".escape($merged['transfer_music_on_hold'])."' style='width: 300px;'>\n";
	echo "		<br /><span class='vtable-hint'>Stream de música de espera durante transferência. <b>Padrão:</b> ".$defaults['transfer_music_on_hold']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Transfer Cache TTL</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='transfer_cache_ttl_seconds' min='60' max='3600' value='".intval($merged['transfer_cache_ttl_seconds'])."' style='width: 100px;'>\n";
	echo "		<span style='margin-left: 5px;'>segundos</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo de cache das regras de transferência. <b>Padrão:</b> ".$defaults['transfer_cache_ttl_seconds']."s</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//====================================================================
	// 📲 CALLBACK SETTINGS
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #388e3c; color: white; font-weight: bold; padding: 12px;'>".($text['header-callback'] ?? '📲 Callback Settings')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Callback Enabled</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input type='checkbox' name='callback_enabled' id='callback_enabled' ".is_checked($merged['callback_enabled']).">\n";
	echo "		<label for='callback_enabled'>Habilitar sistema de callback</label>\n";
	echo "		<br /><span class='vtable-hint'>Permite oferecer retorno de ligação quando não há agentes. <b>Padrão:</b> Habilitado</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Callback Expiration</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='callback_expiration_hours' min='1' max='168' value='".intval($merged['callback_expiration_hours'])."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>horas</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo até expirar um callback não atendido. <b>Padrão:</b> ".$defaults['callback_expiration_hours']."h</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Max Notifications</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='callback_max_notifications' min='1' max='20' value='".intval($merged['callback_max_notifications'])."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>notificações</span>\n";
	echo "		<br /><span class='vtable-hint'>Máximo de notificações por callback. <b>Padrão:</b> ".$defaults['callback_max_notifications']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Min Interval</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='callback_min_interval_minutes' min='1' max='60' value='".intval($merged['callback_min_interval_minutes'])."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>minutos</span>\n";
	echo "		<br /><span class='vtable-hint'>Intervalo mínimo entre notificações. <b>Padrão:</b> ".$defaults['callback_min_interval_minutes']." min</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//====================================================================
	// 🔗 OMNIPLAY INTEGRATION
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #0288d1; color: white; font-weight: bold; padding: 12px;'>".($text['header-omniplay'] ?? '🔗 OmniPlay Integration')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>OmniPlay API URL</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<div style='display: flex; gap: 10px; align-items: center;'>\n";
	echo "			<input class='formfld' type='url' name='omniplay_api_url' value='".escape($merged['omniplay_api_url'])."' style='flex: 1;' placeholder='".$defaults['omniplay_api_url']."'>\n";
	echo button::create(['type'=>'submit','name'=>'test_omniplay','label'=>'Testar','icon'=>'fa-plug','style'=>'padding: 5px 10px;']);
	echo "		</div>\n";
	echo "		<br /><span class='vtable-hint'>URL base da API do OmniPlay. <b>Padrão:</b> ".$defaults['omniplay_api_url']."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>API Timeout</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='omniplay_api_timeout_ms' min='1000' max='60000' step='1000' value='".intval($merged['omniplay_api_timeout_ms'])."' style='width: 100px;'>\n";
	echo "		<span style='margin-left: 5px;'>ms</span>\n";
	echo "		<br /><span class='vtable-hint'>Timeout para requisições à API OmniPlay. <b>Padrão:</b> ".$defaults['omniplay_api_timeout_ms']."ms</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>API Key</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='password' name='omniplay_api_key' value='".escape($merged['omniplay_api_key'])."' autocomplete='new-password' placeholder='Token de autenticação...'>\n";
	echo "		<br /><span class='vtable-hint'>Token para autenticação nas APIs do OmniPlay.</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Webhook URL</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='url' name='omniplay_webhook_url' value='".escape($merged['omniplay_webhook_url'])."' placeholder='https://omniplay.example.com/webhook/voice-ai'>\n";
	echo "		<br /><span class='vtable-hint'>URL para receber eventos de conversas (opcional).</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//====================================================================
	// 💾 DATA MANAGEMENT
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #455a64; color: white; font-weight: bold; padding: 12px;'>".($text['header-data'] ?? '💾 Data Management')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Data Retention</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='data_retention_days' min='1' max='365' value='".intval($merged['data_retention_days'])."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>dias</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo de retenção de logs e conversas. <b>Padrão:</b> ".$defaults['data_retention_days']." dias</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Recording</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input type='checkbox' name='recording_enabled' id='recording_enabled' ".is_checked($merged['recording_enabled']).">\n";
	echo "		<label for='recording_enabled'>Habilitar gravação de chamadas</label>\n";
	echo "		<br /><span class='vtable-hint'>Gravar todas as conversas com a IA. <b>Padrão:</b> Habilitado</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//====================================================================
	// 🔊 AUDIO SETTINGS
	//====================================================================
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #5d4037; color: white; font-weight: bold; padding: 12px;'>".($text['header-audio'] ?? '🔊 Audio Settings')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Sample Rate</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='audio_sample_rate' style='width: 150px;'>\n";
	$rates = [8000 => '8000 Hz (telefonia)', 16000 => '16000 Hz (recomendado)', 24000 => '24000 Hz', 44100 => '44100 Hz (CD)', 48000 => '48000 Hz'];
	foreach ($rates as $rate => $label) {
		$selected = (intval($merged['audio_sample_rate']) === $rate) ? 'selected' : '';
		echo "			<option value='$rate' $selected>$label</option>\n";
	}
	echo "		</select>\n";
	echo "		<br /><span class='vtable-hint'>Taxa de amostragem de áudio. <b>Padrão:</b> ".$defaults['audio_sample_rate']." Hz</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Silence Threshold</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='silence_threshold_ms' min='500' max='10000' step='100' value='".intval($merged['silence_threshold_ms'])."' style='width: 100px;'>\n";
	echo "		<span style='margin-left: 5px;'>ms</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo de silêncio para detectar fim de fala. <b>Padrão:</b> ".$defaults['silence_threshold_ms']."ms</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>Max Recording</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='max_recording_seconds' min='5' max='300' value='".intval($merged['max_recording_seconds'])."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>segundos</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo máximo de gravação por turno. <b>Padrão:</b> ".$defaults['max_recording_seconds']."s</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "</div>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

//include the footer
	require_once "resources/footer.php";

?>
<style>
.vtable-hint {
	font-size: 11px;
	color: #666;
	line-height: 1.4;
}
.vtable-hint b {
	color: #333;
}
</style>