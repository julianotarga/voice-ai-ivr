<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Settings
	Global settings for voice AI.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";
	require_once "resources/check_auth.php";

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

//get current settings
	$database = new database;
	$sql = "SELECT default_setting_subcategory, default_setting_value FROM v_default_settings ";
	$sql .= "WHERE domain_uuid = :domain_uuid ";
	$sql .= "AND default_setting_category = 'voice_secretary' ";
	$sql .= "AND default_setting_enabled = 'true'";
	$parameters['domain_uuid'] = $domain_uuid;
	$rows = $database->select($sql, $parameters, 'all') ?: [];
	unset($sql, $parameters);

	$settings = [];
	foreach ($rows as $row) {
		$settings[$row['default_setting_subcategory']] = $row['default_setting_value'];
	}

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
			'service_url' => $_POST['service_url'] ?? 'http://127.0.0.1:8100/api/v1',
			'data_retention_days' => intval($_POST['data_retention_days'] ?? 90),
			'omniplay_webhook_url' => $_POST['omniplay_webhook_url'] ?? '',
			'omniplay_api_key' => $_POST['omniplay_api_key'] ?? '',
			'max_concurrent_calls' => intval($_POST['max_concurrent_calls'] ?? 10),
			'default_max_turns' => intval($_POST['default_max_turns'] ?? 20),
			'recording_enabled' => isset($_POST['recording_enabled']) ? 'true' : 'false',
		];
		
		foreach ($new_settings as $name => $value) {
			//check if exists
			$sql_check = "SELECT count(*) as cnt FROM v_default_settings ";
			$sql_check .= "WHERE domain_uuid = :domain_uuid ";
			$sql_check .= "AND default_setting_category = 'voice_secretary' ";
			$sql_check .= "AND default_setting_subcategory = :name";
			$check = $database->select($sql_check, [
				'domain_uuid' => $domain_uuid,
				'name' => $name
			], 'row');
			
			if ($check['cnt'] > 0) {
				//update
				$sql_upd = "UPDATE v_default_settings SET default_setting_value = :value ";
				$sql_upd .= "WHERE domain_uuid = :domain_uuid ";
				$sql_upd .= "AND default_setting_category = 'voice_secretary' ";
				$sql_upd .= "AND default_setting_subcategory = :name";
			} else {
				//insert
				$sql_upd = "INSERT INTO v_default_settings ";
				$sql_upd .= "(default_setting_uuid, domain_uuid, default_setting_category, default_setting_subcategory, default_setting_value, default_setting_enabled) ";
				$sql_upd .= "VALUES (uuid_generate_v4(), :domain_uuid, 'voice_secretary', :name, :value, 'true')";
			}
			
			$database->execute($sql_upd, [
				'domain_uuid' => $domain_uuid,
				'name' => $name,
				'value' => $value
			]);
			unset($sql_upd);
		}
		
		message::add($text['message-update']);
		header('Location: settings.php');
		exit;
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

//show the content
	echo "<form method='post' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".($text['title-settings'] ?? 'Settings')."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'submit','name'=>'submit','label'=>$text['button-save'],'icon'=>$_SESSION['theme']['button_icon_save'],'id'=>'btn_save']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-voice_settings'] ?? 'Configure global settings for the Voice AI service.')."\n";
	echo "<br /><br />\n";

	echo "<div class='card'>\n";
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	//Service Configuration Header
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #f5f5f5; font-weight: bold;'>".($text['header-service'] ?? 'Service Configuration')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-service_url'] ?? 'Service URL')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='url' name='service_url' value='".escape($settings['service_url'] ?? 'http://127.0.0.1:8100/api/v1')."'>\n";
	echo "		<br />".($text['description-service_url'] ?? 'URL of the Voice AI service.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-max_concurrent'] ?? 'Max Concurrent Calls')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='max_concurrent_calls' min='1' max='100' value='".intval($settings['max_concurrent_calls'] ?? 10)."'>\n";
	echo "		<br />".($text['description-max_concurrent'] ?? 'Maximum number of simultaneous calls.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-default_max_turns'] ?? 'Default Max Turns')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='default_max_turns' min='1' max='100' value='".intval($settings['default_max_turns'] ?? 20)."'>\n";
	echo "		<br />".($text['description-default_max_turns'] ?? 'Default maximum conversation turns.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//Data Management Header
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #f5f5f5; font-weight: bold;'>".($text['header-data'] ?? 'Data Management')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-retention_days'] ?? 'Data Retention (days)')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='data_retention_days' min='1' max='365' value='".intval($settings['data_retention_days'] ?? 90)."'>\n";
	echo "		<br />".($text['description-retention'] ?? 'Number of days to keep conversation data.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-recording'] ?? 'Recording Enabled')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$recording_checked = (($settings['recording_enabled'] ?? 'false') === 'true') ? 'checked' : '';
	echo "		<input type='checkbox' name='recording_enabled' ".$recording_checked.">\n";
	echo "		<span>".($text['description-recording'] ?? 'Enable call recording for conversations.')."\n</span>";
	echo "	</td>\n";
	echo "</tr>\n";

	//OmniPlay Integration Header
	echo "<tr>\n";
	echo "	<td colspan='2' class='vtable' style='background: #f5f5f5; font-weight: bold;'>".($text['header-omniplay'] ?? 'OmniPlay Integration')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-webhook_url'] ?? 'Webhook URL')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='url' name='omniplay_webhook_url' value='".escape($settings['omniplay_webhook_url'] ?? '')."' placeholder='https://omniplay.example.com/webhook/voice-ai'>\n";
	echo "		<br />".($text['description-omniplay_webhook'] ?? 'URL to send conversation events.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-api_key'] ?? 'API Key')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='password' name='omniplay_api_key' value='".escape($settings['omniplay_api_key'] ?? '')."' autocomplete='new-password'>\n";
	echo "		<br />".($text['description-omniplay_key'] ?? 'API key for OmniPlay authentication.')."\n";
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
