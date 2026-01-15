<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Virtual Secretary with AI
	Create or edit a virtual secretary.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";

//check permissions
	if (permission_exists('voice_secretary_add') || permission_exists('voice_secretary_edit')) {
		//access granted
	}
	else {
		echo "access denied";
		exit;
	}

//add multi-lingual support
	$language = new text;
	$text = $language->get();

//include class
	require_once "resources/classes/voice_secretary.php";

//get domain_uuid from session
	$domain_uuid = $_SESSION['domain_uuid'] ?? null;
	if (!$domain_uuid) {
		echo "access denied";
		exit;
	}

//initialize
	$secretary_obj = new voice_secretary;
	$action = 'add';
	$data = [];

//check if editing existing
	if (isset($_GET['id']) && is_uuid($_GET['id'])) {
		$action = 'edit';
		$secretary_uuid = $_GET['id'];
		$data = $secretary_obj->get($secretary_uuid, $domain_uuid);
		
		if (!$data) {
			message::add($text['message-invalid_id'] ?? 'Invalid ID', 'negative');
			header('Location: secretary.php');
			exit;
		}
	}

//process form submission
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && count($_POST) > 0) {
		//validate token
		$token_obj = new token;
		if (!$token_obj->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: secretary.php');
			exit;
		}

		//collect form data - normalizar quebras de linha (Windows \r\n -> Unix \n)
		$form_data = [
			'secretary_name' => trim($_POST['secretary_name'] ?? ''),
			'company_name' => trim($_POST['company_name'] ?? ''),
			'system_prompt' => str_replace("\r\n", "\n", trim($_POST['system_prompt'] ?? '')),
			'greeting_message' => str_replace("\r\n", "\n", trim($_POST['greeting_message'] ?? '')),
			'farewell_message' => str_replace("\r\n", "\n", trim($_POST['farewell_message'] ?? '')),
			'processing_mode' => $_POST['processing_mode'] ?? 'turn_based',
			'realtime_provider_uuid' => !empty($_POST['realtime_provider_uuid']) ? $_POST['realtime_provider_uuid'] : null,
			'extension' => $_POST['extension'] ?? '',
			'stt_provider_uuid' => !empty($_POST['stt_provider_uuid']) ? $_POST['stt_provider_uuid'] : null,
			'tts_provider_uuid' => !empty($_POST['tts_provider_uuid']) ? $_POST['tts_provider_uuid'] : null,
			'llm_provider_uuid' => !empty($_POST['llm_provider_uuid']) ? $_POST['llm_provider_uuid'] : null,
			'embeddings_provider_uuid' => !empty($_POST['embeddings_provider_uuid']) ? $_POST['embeddings_provider_uuid'] : null,
			'tts_voice' => $_POST['tts_voice'] ?? '',
			'language' => $_POST['language'] ?? 'pt-BR',
			'max_turns' => intval($_POST['max_turns'] ?? 20),
			'transfer_extension' => $_POST['transfer_extension'] ?? '200',
			'handoff_timeout' => intval($_POST['handoff_timeout'] ?? 30),
			'presence_check_enabled' => isset($_POST['presence_check_enabled']) ? 'true' : 'false',
			'time_condition_uuid' => !empty($_POST['time_condition_uuid']) ? $_POST['time_condition_uuid'] : null,
			'enabled' => $_POST['enabled'] ?? 'true',
			'webhook_url' => $_POST['webhook_url'] ?? '',
			// Handoff OmniPlay settings
			'handoff_enabled' => isset($_POST['handoff_enabled']) ? 'true' : 'false',
			'handoff_keywords' => trim($_POST['handoff_keywords'] ?? 'atendente,humano,pessoa,operador'),
			'fallback_ticket_enabled' => isset($_POST['fallback_ticket_enabled']) ? 'true' : 'false',
			'handoff_queue_id' => !empty($_POST['handoff_queue_id']) ? intval($_POST['handoff_queue_id']) : null,
			// OmniPlay Integration
			'omniplay_company_id' => !empty($_POST['omniplay_company_id']) ? intval($_POST['omniplay_company_id']) : null,
		];
		
		//validate
		if (empty($form_data['secretary_name'])) {
			message::add($text['message-required'].' '.($text['label-secretary_name'] ?? 'Name'), 'negative');
		} 
		else {
			//build array for FusionPBX database save
			if ($action === 'add') {
				$secretary_uuid = uuid();
			}
			
			$array['voice_secretaries'][0]['voice_secretary_uuid'] = $secretary_uuid;
			$array['voice_secretaries'][0]['domain_uuid'] = $domain_uuid;
			$array['voice_secretaries'][0]['secretary_name'] = $form_data['secretary_name'];
			$array['voice_secretaries'][0]['company_name'] = $form_data['company_name'] ?: null;
			$array['voice_secretaries'][0]['extension'] = $form_data['extension'] ?: null;
			$array['voice_secretaries'][0]['processing_mode'] = $form_data['processing_mode'];
			$array['voice_secretaries'][0]['personality_prompt'] = $form_data['system_prompt'] ?: null;
			$array['voice_secretaries'][0]['greeting_message'] = $form_data['greeting_message'] ?: null;
			$array['voice_secretaries'][0]['farewell_message'] = $form_data['farewell_message'] ?: null;
			$array['voice_secretaries'][0]['stt_provider_uuid'] = $form_data['stt_provider_uuid'] ?: null;
			$array['voice_secretaries'][0]['tts_provider_uuid'] = $form_data['tts_provider_uuid'] ?: null;
			$array['voice_secretaries'][0]['llm_provider_uuid'] = $form_data['llm_provider_uuid'] ?: null;
			$array['voice_secretaries'][0]['embeddings_provider_uuid'] = $form_data['embeddings_provider_uuid'] ?: null;
			$array['voice_secretaries'][0]['realtime_provider_uuid'] = $form_data['realtime_provider_uuid'] ?: null;
			$array['voice_secretaries'][0]['tts_voice_id'] = $form_data['tts_voice'] ?: null;
			$array['voice_secretaries'][0]['language'] = $form_data['language'];
			$array['voice_secretaries'][0]['max_turns'] = $form_data['max_turns'];
			$array['voice_secretaries'][0]['transfer_extension'] = $form_data['transfer_extension'];
			$array['voice_secretaries'][0]['handoff_timeout'] = $form_data['handoff_timeout'];
			$array['voice_secretaries'][0]['presence_check_enabled'] = $form_data['presence_check_enabled'];
			$array['voice_secretaries'][0]['time_condition_uuid'] = $form_data['time_condition_uuid'];
			$array['voice_secretaries'][0]['enabled'] = $form_data['enabled'];
			$array['voice_secretaries'][0]['omniplay_webhook_url'] = $form_data['webhook_url'] ?: null;
			// Handoff OmniPlay settings
			$array['voice_secretaries'][0]['handoff_enabled'] = $form_data['handoff_enabled'];
			$array['voice_secretaries'][0]['handoff_keywords'] = $form_data['handoff_keywords'] ?: null;
			$array['voice_secretaries'][0]['fallback_ticket_enabled'] = $form_data['fallback_ticket_enabled'];
			$array['voice_secretaries'][0]['handoff_queue_id'] = $form_data['handoff_queue_id'] ?: null;
			$array['voice_secretaries'][0]['omniplay_company_id'] = $form_data['omniplay_company_id'] ?: null;
			
			//add permissions
			$p = permissions::new();
			$p->add('voice_secretary_add', 'temp');
			$p->add('voice_secretary_edit', 'temp');
			
			//save using FusionPBX database class
			$database = new database;
			$database->app_name = 'voice_secretary';
			$database->app_uuid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
			$database->save($array);
			unset($array);
			
			//remove temp permissions
			$p->delete('voice_secretary_add', 'temp');
			$p->delete('voice_secretary_edit', 'temp');
			
			//set message and redirect
			if ($action === 'add') {
				message::add($text['message-add']);
			} else {
				message::add($text['message-update']);
			}
			header('Location: secretary.php');
			exit;
		}
	}

//get providers for dropdowns
	$stt_providers = $secretary_obj->get_providers('stt', $domain_uuid) ?: [];
	$tts_providers = $secretary_obj->get_providers('tts', $domain_uuid) ?: [];
	$llm_providers = $secretary_obj->get_providers('llm', $domain_uuid) ?: [];
	$embeddings_providers = $secretary_obj->get_providers('embeddings', $domain_uuid) ?: [];
	$realtime_providers = $secretary_obj->get_providers('realtime', $domain_uuid) ?: [];

//get time conditions for dropdown
	$database = new database;
	$sql = "SELECT time_condition_uuid, time_condition_name FROM v_time_conditions WHERE domain_uuid = :domain_uuid AND time_condition_enabled = 'true' ORDER BY time_condition_name";
	$parameters['domain_uuid'] = $domain_uuid;
	$time_conditions = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = ($action === 'add') 
		? ($text['title-voice_secretary_add'] ?? 'Add Secretary') 
		: ($text['title-voice_secretary_edit'] ?? 'Edit Secretary');
	require_once "resources/header.php";

//show the content
	echo "<form method='post' name='frm' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'button','label'=>$text['button-back'],'icon'=>$_SESSION['theme']['button_icon_back'],'id'=>'btn_back','link'=>'secretary.php']);
	echo button::create(['type'=>'submit','label'=>$text['button-save'],'icon'=>$_SESSION['theme']['button_icon_save'],'id'=>'btn_save','style'=>'margin-left: 15px;']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-voice_secretary'] ?? 'Configure a virtual secretary with AI.')."\n";
	echo "<br /><br />\n";

	echo "<div class='card'>\n";
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	//Basic Info Section
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-basic_info'] ?? 'Basic Information')."</b>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-secretary_name'] ?? 'Name')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='secretary_name' maxlength='255' value='".escape($data['secretary_name'] ?? '')."' required>\n";
	echo "		<br />".($text['description-secretary_name'] ?? 'Enter a name for this secretary.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-company_name'] ?? 'Company')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='company_name' maxlength='255' value='".escape($data['company_name'] ?? '')."'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-extension'] ?? 'Extension')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='extension' maxlength='20' value='".escape($data['extension'] ?? '')."' placeholder='8000'>\n";
	echo "		<br />".($text['description-extension'] ?? 'Extension number for this secretary.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-language'] ?? 'Language')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='language'>\n";
	$lang = $data['language'] ?? 'pt-BR';
	echo "			<option value='pt-BR' ".($lang === 'pt-BR' ? 'selected' : '').">Português (Brasil)</option>\n";
	echo "			<option value='en-US' ".($lang === 'en-US' ? 'selected' : '').">English (US)</option>\n";
	echo "			<option value='es-ES' ".($lang === 'es-ES' ? 'selected' : '').">Español</option>\n";
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-enabled'] ?? 'Enabled')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	//normalize enabled value to string for comparison
	$enabled_raw = $data['enabled'] ?? 'true';
	$is_enabled = ($enabled_raw === true || $enabled_raw === 'true' || $enabled_raw === 't' || $enabled_raw === '1' || $enabled_raw === 1);
	echo "		<select class='formfld' name='enabled'>\n";
	echo "			<option value='true' ".($is_enabled ? 'selected' : '').">".$text['label-true']."</option>\n";
	echo "			<option value='false' ".(!$is_enabled ? 'selected' : '').">".$text['label-false']."</option>\n";
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//Processing Mode Section
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-processing_mode'] ?? 'Processing Mode')."</b>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-mode'] ?? 'Mode')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$mode = $data['processing_mode'] ?? 'turn_based';
	echo "		<select class='formfld' name='processing_mode' id='processing_mode' onchange='toggleRealtimeProvider()'>\n";
	echo "			<option value='turn_based' ".($mode === 'turn_based' ? 'selected' : '').">Turn-based (v1)</option>\n";
	echo "			<option value='realtime' ".($mode === 'realtime' ? 'selected' : '').">Realtime (v2)</option>\n";
	echo "			<option value='auto' ".($mode === 'auto' ? 'selected' : '').">Auto</option>\n";
	echo "		</select>\n";
	echo "		<br />".($text['description-processing_mode'] ?? 'Turn-based: traditional IVR. Realtime: natural conversation.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr id='realtime_provider_row' style='display: none;'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-realtime_provider'] ?? 'Realtime Provider')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='realtime_provider_uuid'>\n";
	echo "			<option value=''>".($text['option-select'] ?? '-- Select --')."</option>\n";
	foreach ($realtime_providers as $p) {
		$selected = (($data['realtime_provider_uuid'] ?? '') === $p['voice_ai_provider_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($p['voice_ai_provider_uuid'])."' ".$selected.">".escape($p['provider_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//Prompts Section
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-prompts'] ?? 'AI Prompts')."</b>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//normalizar quebras de linha ao exibir (remover \r duplicados)
	$personality_prompt = str_replace("\r\n", "\n", str_replace("\r", "", $data['personality_prompt'] ?? ''));
	$greeting_msg = str_replace("\r\n", "\n", str_replace("\r", "", $data['greeting_message'] ?? 'Olá! Como posso ajudar?'));
	$farewell_msg = str_replace("\r\n", "\n", str_replace("\r", "", $data['farewell_message'] ?? 'Foi um prazer ajudar! Até logo!'));

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-personality_prompt'] ?? 'Personality Prompt')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<textarea class='formfld' name='system_prompt' rows='8' style='width: 100%; min-height: 200px;'>".escape(trim($personality_prompt))."</textarea>\n";
	echo "		<br />".($text['description-personality_prompt'] ?? 'Instructions for the AI personality.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-greeting'] ?? 'Greeting')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<textarea class='formfld' name='greeting_message' rows='3' style='width: 100%;'>".escape(trim($greeting_msg))."</textarea>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-farewell'] ?? 'Farewell')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<textarea class='formfld' name='farewell_message' rows='3' style='width: 100%;'>".escape(trim($farewell_msg))."</textarea>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//AI Providers Section
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-providers'] ?? 'AI Providers')."</b>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-stt_provider'] ?? 'STT Provider')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='stt_provider_uuid'>\n";
	echo "			<option value=''>".($text['option-default'] ?? 'Default')."</option>\n";
	foreach ($stt_providers as $p) {
		$selected = (($data['stt_provider_uuid'] ?? '') === $p['voice_ai_provider_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($p['voice_ai_provider_uuid'])."' ".$selected.">".escape($p['provider_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-tts_provider'] ?? 'TTS Provider')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='tts_provider_uuid' id='tts_provider_uuid' onchange='loadTtsVoices(true)'>\n";
	echo "			<option value=''>".($text['option-default'] ?? 'Default')."</option>\n";
	foreach ($tts_providers as $p) {
		$selected = (($data['tts_provider_uuid'] ?? '') === $p['voice_ai_provider_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($p['voice_ai_provider_uuid'])."' ".$selected.">".escape($p['provider_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-tts_voice'] ?? 'TTS Voice')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='tts_voice' id='tts_voice' maxlength='200' value='".escape($data['tts_voice_id'] ?? '')."' placeholder='ex: nova, alloy'>\n";
	echo "		<select class='formfld' id='tts_voice_select' style='display: none; margin-left: 10px;' onchange=\"document.getElementById('tts_voice').value = this.value;\">\n";
	echo "			<option value=''>".($text['option-select'] ?? '-- Select --')."</option>\n";
	echo "		</select>\n";
	echo button::create(['type'=>'button','label'=>($text['button-load_voices'] ?? 'Load Voices'),'icon'=>'sync','style'=>'margin-left: 10px;','onclick'=>'loadTtsVoices(false)']);
	echo "		<div id='tts_voice_status' style='margin-top: 5px; font-size: 11px; color: #666;'></div>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-llm_provider'] ?? 'LLM Provider')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='llm_provider_uuid'>\n";
	echo "			<option value=''>".($text['option-default'] ?? 'Default')."</option>\n";
	foreach ($llm_providers as $p) {
		$selected = (($data['llm_provider_uuid'] ?? '') === $p['voice_ai_provider_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($p['voice_ai_provider_uuid'])."' ".$selected.">".escape($p['provider_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-embeddings_provider'] ?? 'Embeddings')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='embeddings_provider_uuid'>\n";
	echo "			<option value=''>".($text['option-default'] ?? 'Default')."</option>\n";
	foreach ($embeddings_providers as $p) {
		$selected = (($data['embeddings_provider_uuid'] ?? '') === $p['voice_ai_provider_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($p['voice_ai_provider_uuid'])."' ".$selected.">".escape($p['provider_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//Transfer & Handoff Settings Section
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-transfer'] ?? 'Transfer & Handoff Settings')."</b>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Handoff Enabled
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-handoff_enabled'] ?? 'Enable Handoff')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$handoff_enabled = (!isset($data['handoff_enabled']) || $data['handoff_enabled'] == 'true' || $data['handoff_enabled'] === true);
	echo "		<input type='checkbox' name='handoff_enabled' id='handoff_enabled' ".($handoff_enabled ? 'checked' : '')." onchange='toggleHandoffOptions()'>\n";
	echo "		<label for='handoff_enabled'>".($text['label-enabled'] ?? 'Enabled')."</label>\n";
	echo "		<br />".($text['description-handoff_enabled'] ?? 'Allow transferring calls to human agents or creating tickets')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-transfer_extension'] ?? 'Transfer Extension')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='transfer_extension' maxlength='20' value='".escape($data['transfer_extension'] ?? '200')."'>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-handoff_timeout'] ?? 'Handoff Timeout (seconds)')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='handoff_timeout' min='5' max='120' value='".intval($data['handoff_timeout'] ?? 30)."'>\n";
	echo "		<br />".($text['description-handoff_timeout'] ?? 'Timeout in seconds before fallback to ticket (default: 30)')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-presence_check'] ?? 'Check Extension Presence')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$presence_enabled = (!isset($data['presence_check_enabled']) || $data['presence_check_enabled'] == 'true' || $data['presence_check_enabled'] === true);
	echo "		<input type='checkbox' name='presence_check_enabled' id='presence_check_enabled' ".($presence_enabled ? 'checked' : '').">\n";
	echo "		<label for='presence_check_enabled'>".($text['label-enabled'] ?? 'Enabled')."</label>\n";
	echo "		<br />".($text['description-presence_check'] ?? 'Check if extension is online before transferring')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-time_condition'] ?? 'Business Hours')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='time_condition_uuid'>\n";
	echo "			<option value=''>".($text['option-no_restriction'] ?? '-- No restriction --')."</option>\n";
	foreach ($time_conditions as $tc) {
		$selected = (($data['time_condition_uuid'] ?? '') === $tc['time_condition_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($tc['time_condition_uuid'])."' ".$selected.">".escape($tc['time_condition_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "		<br />".($text['description-time_condition'] ?? 'Only transfer during these hours, otherwise create ticket')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-max_turns'] ?? 'Max AI Turns')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='max_turns' min='1' max='100' value='".escape($data['max_turns'] ?? 20)."'>\n";
	echo "		<br />".($text['description-max_turns'] ?? 'After this many turns, automatically initiate handoff')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Handoff Keywords
	echo "<tr class='handoff-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-handoff_keywords'] ?? 'Handoff Keywords')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$keywords = $data['handoff_keywords'] ?? 'atendente,humano,pessoa,operador,falar com alguém';
	echo "		<input class='formfld' type='text' name='handoff_keywords' maxlength='500' value='".escape($keywords)."' style='width: 100%;'>\n";
	echo "		<br />".($text['description-handoff_keywords'] ?? 'Comma-separated keywords that trigger handoff (e.g., "atendente,humano,pessoa")')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback Ticket
	echo "<tr class='handoff-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_ticket'] ?? 'Fallback to Ticket')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$fallback_enabled = (!isset($data['fallback_ticket_enabled']) || $data['fallback_ticket_enabled'] == 'true' || $data['fallback_ticket_enabled'] === true);
	echo "		<input type='checkbox' name='fallback_ticket_enabled' id='fallback_ticket_enabled' ".($fallback_enabled ? 'checked' : '').">\n";
	echo "		<label for='fallback_ticket_enabled'>".($text['label-enabled'] ?? 'Enabled')."</label>\n";
	echo "		<br />".($text['description-fallback_ticket'] ?? 'Create a pending ticket when no agents are online or transfer fails')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Handoff Queue (for ticket assignment)
	echo "<tr class='handoff-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-handoff_queue'] ?? 'Ticket Queue')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='handoff_queue_id' maxlength='20' value='".escape($data['handoff_queue_id'] ?? '')."' placeholder='Queue ID from OmniPlay'>\n";
	echo "		<br />".($text['description-handoff_queue'] ?? 'OmniPlay queue ID to assign tickets created by voice handoff')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//Integration Section
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-integration'] ?? 'Integration')."</b>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-webhook_url'] ?? 'Webhook URL')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='url' name='webhook_url' maxlength='500' value='".escape($data['omniplay_webhook_url'] ?? '')."' placeholder='https://...'>\n";
	echo "		<br />".($text['description-webhook_url'] ?? 'OmniPlay webhook URL for creating tickets.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-omniplay_company_id'] ?? 'OmniPlay Company ID')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='omniplay_company_id' min='1' value='".intval($data['omniplay_company_id'] ?? '')."' placeholder='1'>\n";
	echo "		<br />".($text['description-omniplay_company_id'] ?? 'OmniPlay Company ID for API integration (required for handoff tickets).')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "</div>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

?>

<script>
function toggleRealtimeProvider() {
	var mode = document.getElementById('processing_mode').value;
	var row = document.getElementById('realtime_provider_row');
	if (mode === 'realtime' || mode === 'auto') {
		row.style.display = '';
	} else {
		row.style.display = 'none';
	}
}

function toggleHandoffOptions() {
	var enabled = document.getElementById('handoff_enabled').checked;
	var rows = document.querySelectorAll('.handoff-option');
	rows.forEach(function(row) {
		row.style.display = enabled ? '' : 'none';
	});
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
	toggleRealtimeProvider();
	toggleHandoffOptions();
});

async function loadTtsVoices(silent) {
	try {
		var providerUuid = document.getElementById('tts_provider_uuid')?.value || '';
		var language = document.querySelector('select[name="language"]')?.value || 'pt-BR';
		var select = document.getElementById('tts_voice_select');
		var status = document.getElementById('tts_voice_status');

		if (!providerUuid) {
			if (!silent) {
				status.textContent = '<?php echo addslashes($text['message-select_tts_provider'] ?? 'Select a TTS Provider to list voices.'); ?>';
			}
			select.style.display = 'none';
			return;
		}

		status.textContent = silent ? '' : '<?php echo addslashes($text['message-loading'] ?? 'Loading...'); ?>';
		select.style.display = 'none';
		select.innerHTML = '<option value=""><?php echo addslashes($text['option-select'] ?? '-- Select --'); ?></option>';

		const resp = await fetch('tts_voices.php?provider_uuid=' + encodeURIComponent(providerUuid) + '&language=' + encodeURIComponent(language));
		const data = await resp.json();
		if (!data.success) {
			status.textContent = '<?php echo addslashes($text['message-load_failed'] ?? 'Failed to load voices'); ?>: ' + (data.message || 'error');
			return;
		}

		const voices = data.voices || [];
		if (!Array.isArray(voices) || voices.length === 0) {
			status.textContent = '<?php echo addslashes($text['message-no_voices'] ?? 'No voices returned.'); ?>';
			return;
		}

		voices.forEach(v => {
			const opt = document.createElement('option');
			opt.value = v.voice_id;
			const label = (v.name ? v.name + ' - ' : '') + v.voice_id + (v.gender ? ' (' + v.gender + ')' : '');
			opt.textContent = label;
			select.appendChild(opt);
		});

		select.style.display = '';
		status.textContent = '<?php echo addslashes($text['message-voices_loaded'] ?? 'Voices loaded'); ?>: ' + voices.length;
	} catch (e) {
		var status = document.getElementById('tts_voice_status');
		if (status) status.textContent = '<?php echo addslashes($text['message-error'] ?? 'Error loading voices.'); ?>';
	}
}

//initialize on page load
document.addEventListener('DOMContentLoaded', function() {
	toggleRealtimeProvider();
	loadTtsVoices(true);
});
</script>

<?php

//include the footer
	require_once "resources/footer.php";

?>
