<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Settings
	Global settings for voice AI.
	MULTI-TENANT: Uses domain_uuid from session.
	
	DEFAULT VALUES synchronized with voice-ai-service/config/settings.py
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

// DEFAULT VALUES - Synchronized with Python settings.py
$defaults = [
	// Service Configuration
	'service_url' => 'http://127.0.0.1:8100/api/v1',
	'max_concurrent_calls' => 10,
	'default_max_turns' => 20,
	
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
	
	// Callback Settings
	'callback_enabled' => 'true',
	'callback_expiration_hours' => 24,
	'callback_max_notifications' => 5,
	
	// OmniPlay Integration
	'omniplay_api_url' => 'http://127.0.0.1:8080',
	'omniplay_api_timeout_ms' => 10000,
	'omniplay_api_key' => '',
	'omniplay_webhook_url' => '',
	
	// Data Management
	'data_retention_days' => 90,
	'recording_enabled' => 'true',
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

//merge with defaults
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
			'service_url' => trim($_POST['service_url'] ?? $defaults['service_url']),
			'max_concurrent_calls' => intval($_POST['max_concurrent_calls'] ?? $defaults['max_concurrent_calls']),
			'default_max_turns' => intval($_POST['default_max_turns'] ?? $defaults['default_max_turns']),
			'esl_host' => trim($_POST['esl_host'] ?? $defaults['esl_host']),
			'esl_port' => intval($_POST['esl_port'] ?? $defaults['esl_port']),
			'esl_password' => $_POST['esl_password'] ?? $defaults['esl_password'],
			'esl_connect_timeout' => floatval($_POST['esl_connect_timeout'] ?? $defaults['esl_connect_timeout']),
			'esl_read_timeout' => floatval($_POST['esl_read_timeout'] ?? $defaults['esl_read_timeout']),
			'transfer_default_timeout' => intval($_POST['transfer_default_timeout'] ?? $defaults['transfer_default_timeout']),
			'transfer_announce_enabled' => isset($_POST['transfer_announce_enabled']) ? 'true' : 'false',
			'transfer_music_on_hold' => trim($_POST['transfer_music_on_hold'] ?? $defaults['transfer_music_on_hold']),
			'callback_enabled' => isset($_POST['callback_enabled']) ? 'true' : 'false',
			'callback_expiration_hours' => intval($_POST['callback_expiration_hours'] ?? $defaults['callback_expiration_hours']),
			'callback_max_notifications' => intval($_POST['callback_max_notifications'] ?? $defaults['callback_max_notifications']),
			'omniplay_api_url' => trim($_POST['omniplay_api_url'] ?? $defaults['omniplay_api_url']),
			'omniplay_api_timeout_ms' => intval($_POST['omniplay_api_timeout_ms'] ?? $defaults['omniplay_api_timeout_ms']),
			'omniplay_api_key' => $_POST['omniplay_api_key'] ?? '',
			'omniplay_webhook_url' => trim($_POST['omniplay_webhook_url'] ?? ''),
			'data_retention_days' => intval($_POST['data_retention_days'] ?? $defaults['data_retention_days']),
			'recording_enabled' => isset($_POST['recording_enabled']) ? 'true' : 'false',
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
		
		// Notify Voice AI Service to reload settings
		$service_url = $new_settings['service_url'] ?? $defaults['service_url'];
		$reload_url = rtrim($service_url, '/') . '/callback/settings/reload';
		
		$ch = curl_init($reload_url);
		curl_setopt($ch, CURLOPT_POST, true);
		curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query(['domain_uuid' => $domain_uuid]));
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
		curl_setopt($ch, CURLOPT_TIMEOUT, 5);
		curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
		$reload_response = curl_exec($ch);
		$reload_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		curl_close($ch);
		
		if ($reload_code >= 200 && $reload_code < 300) {
			message::add($text['message-update'] ?? 'Settings saved and reloaded.');
		} else {
			message::add($text['message-update'] ?? 'Settings saved.');
		}
		
		header('Location: settings.php');
		exit;
	}

//handle test connections
	$test_result = null;
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['test_esl'])) {
		$esl_host = trim($_POST['esl_host'] ?? $merged['esl_host']);
		$esl_port = intval($_POST['esl_port'] ?? $merged['esl_port']);
		
		$fp = @fsockopen($esl_host, $esl_port, $errno, $errstr, 3);
		if ($fp) {
			fclose($fp);
			$test_result = ['type' => 'positive', 'message' => "ESL connection successful! ($esl_host:$esl_port)"];
		} else {
			$test_result = ['type' => 'negative', 'message' => "ESL connection failed: $errstr ($errno)"];
		}
	}
	
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['test_service'])) {
		$service_url = trim($_POST['service_url'] ?? $merged['service_url']);
		
		$ch = curl_init($service_url . '/health');
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
		curl_setopt($ch, CURLOPT_TIMEOUT, 5);
		curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
		$response = curl_exec($ch);
		$http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
		$curl_error = curl_error($ch);
		curl_close($ch);
		
		if ($http_code >= 200 && $http_code < 400) {
			$test_result = ['type' => 'positive', 'message' => "Voice AI Service responding! (HTTP $http_code)"];
		} else {
			$test_result = ['type' => 'negative', 'message' => "Voice AI Service not responding: " . ($curl_error ?: "HTTP $http_code")];
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

//show test result
	if ($test_result) {
		message::add($test_result['message'], $test_result['type']);
	}

//helper function
function is_checked($value) {
	return ($value === 'true' || $value === true || $value === '1') ? 'checked' : '';
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

	echo "<div class='card'>\n";
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	// SERVICE CONFIGURATION
	echo "<tr><td colspan='2' style='background: #1976d2; color: white; font-weight: bold; padding: 12px;'>Service Configuration</td></tr>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncell'>Voice AI Service URL</td>\n";
	echo "	<td width='70%' class='vtable'>\n";
	echo "		<div style='display: flex; gap: 10px;'>\n";
	echo "			<input class='formfld' type='url' name='service_url' value='".escape($merged['service_url'])."' style='flex: 1;'>\n";
	echo button::create(['type'=>'submit','name'=>'test_service','label'=>'Test','style'=>'padding: 5px 10px;']);
	echo "		</div>\n";
	echo "		<br /><small>Default: ".$defaults['service_url']."</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Max Concurrent Calls</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='max_concurrent_calls' min='1' max='100' value='".intval($merged['max_concurrent_calls'])."' style='width: 100px;'>\n";
	echo "		<small>Default: ".$defaults['max_concurrent_calls']."</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Default Max Turns</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='default_max_turns' min='1' max='100' value='".intval($merged['default_max_turns'])."' style='width: 100px;'>\n";
	echo "		<small>Default: ".$defaults['default_max_turns']."</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// ESL CONFIGURATION
	echo "<tr><td colspan='2' style='background: #e65100; color: white; font-weight: bold; padding: 12px;'>ESL Configuration (FreeSWITCH)</td></tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>ESL Host</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='text' name='esl_host' value='".escape($merged['esl_host'])."' style='width: 200px;'>\n";
	echo "		<small>Default: ".$defaults['esl_host']."</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>ESL Port</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<div style='display: flex; gap: 10px;'>\n";
	echo "			<input class='formfld' type='number' name='esl_port' value='".intval($merged['esl_port'])."' style='width: 100px;'>\n";
	echo button::create(['type'=>'submit','name'=>'test_esl','label'=>'Test Connection','style'=>'padding: 5px 10px;']);
	echo "		</div>\n";
	echo "		<small>Default: ".$defaults['esl_port']."</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>ESL Password</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='password' name='esl_password' value='".escape($merged['esl_password'])."' style='width: 200px;' autocomplete='new-password'>\n";
	echo "		<small>Default: ".$defaults['esl_password']."</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>ESL Timeouts</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		Connect: <input class='formfld' type='number' name='esl_connect_timeout' value='".floatval($merged['esl_connect_timeout'])."' style='width: 70px;' step='0.5'>s\n";
	echo "		Read: <input class='formfld' type='number' name='esl_read_timeout' value='".floatval($merged['esl_read_timeout'])."' style='width: 70px;'>s\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// TRANSFER SETTINGS
	echo "<tr><td colspan='2' style='background: #7b1fa2; color: white; font-weight: bold; padding: 12px;'>Transfer Settings</td></tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Transfer Timeout</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='transfer_default_timeout' min='5' max='120' value='".intval($merged['transfer_default_timeout'])."' style='width: 80px;'> seconds\n";
	echo "		<small>Default: ".$defaults['transfer_default_timeout']."s</small>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Announce Transfer</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input type='checkbox' name='transfer_announce_enabled' id='transfer_announce' ".is_checked($merged['transfer_announce_enabled']).">\n";
	echo "		<label for='transfer_announce'>Announce transfer to caller</label>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Music on Hold</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='text' name='transfer_music_on_hold' value='".escape($merged['transfer_music_on_hold'])."' style='width: 300px;'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// CALLBACK SETTINGS
	echo "<tr><td colspan='2' style='background: #388e3c; color: white; font-weight: bold; padding: 12px;'>Callback Settings</td></tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Callback Enabled</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input type='checkbox' name='callback_enabled' id='callback_enabled' ".is_checked($merged['callback_enabled']).">\n";
	echo "		<label for='callback_enabled'>Enable callback system</label>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Callback Expiration</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='callback_expiration_hours' min='1' max='168' value='".intval($merged['callback_expiration_hours'])."' style='width: 80px;'> hours\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Max Notifications</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='callback_max_notifications' min='1' max='20' value='".intval($merged['callback_max_notifications'])."' style='width: 80px;'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// OMNIPLAY INTEGRATION
	echo "<tr><td colspan='2' style='background: #0288d1; color: white; font-weight: bold; padding: 12px;'>OmniPlay Integration</td></tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>OmniPlay API URL</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='url' name='omniplay_api_url' value='".escape($merged['omniplay_api_url'])."' style='width: 100%;'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>API Timeout</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='omniplay_api_timeout_ms' value='".intval($merged['omniplay_api_timeout_ms'])."' style='width: 100px;'> ms\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>API Key</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='password' name='omniplay_api_key' value='".escape($merged['omniplay_api_key'])."' autocomplete='new-password'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Webhook URL</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='url' name='omniplay_webhook_url' value='".escape($merged['omniplay_webhook_url'])."'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// DATA MANAGEMENT
	echo "<tr><td colspan='2' style='background: #455a64; color: white; font-weight: bold; padding: 12px;'>Data Management</td></tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Data Retention</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input class='formfld' type='number' name='data_retention_days' min='1' max='365' value='".intval($merged['data_retention_days'])."' style='width: 80px;'> days\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell'>Recording</td>\n";
	echo "	<td class='vtable'>\n";
	echo "		<input type='checkbox' name='recording_enabled' id='recording' ".is_checked($merged['recording_enabled']).">\n";
	echo "		<label for='recording'>Enable call recording</label>\n";
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
