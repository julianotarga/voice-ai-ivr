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
	require_once "resources/classes/omniplay_api_client.php";

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

//load OmniPlay integration data
	$omniplay_client = new OmniPlayAPIClient($domain_uuid, $db);
	$omniplay_queues = [];
	$omniplay_users = [];
	$omniplay_configured = $omniplay_client->isConfigured();
	
	if ($omniplay_configured) {
		$omniplay_queues = $omniplay_client->getQueues();
		$omniplay_users = $omniplay_client->getUsers();
	}

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
			'fallback_action' => $_POST['fallback_action'] ?? 'ticket',
			'handoff_queue_id' => !empty($_POST['handoff_queue_id']) ? intval($_POST['handoff_queue_id']) : null,
			'fallback_user_id' => !empty($_POST['fallback_user_id']) ? intval($_POST['fallback_user_id']) : null,
			'fallback_priority' => $_POST['fallback_priority'] ?? 'medium',
			'fallback_notify_enabled' => isset($_POST['fallback_notify_enabled']) ? 'true' : 'false',
			// OmniPlay Integration
			'omniplay_company_id' => !empty($_POST['omniplay_company_id']) ? intval($_POST['omniplay_company_id']) : null,
			// Audio Configuration
			'audio_warmup_chunks' => intval($_POST['audio_warmup_chunks'] ?? 15),
			'audio_warmup_ms' => intval($_POST['audio_warmup_ms'] ?? 400),
			'audio_adaptive_warmup' => isset($_POST['audio_adaptive_warmup']) ? 'true' : 'false',
			'jitter_buffer_min' => intval($_POST['jitter_buffer_min'] ?? 100),
			'jitter_buffer_max' => intval($_POST['jitter_buffer_max'] ?? 300),
			'jitter_buffer_step' => intval($_POST['jitter_buffer_step'] ?? 40),
			'stream_buffer_size' => intval($_POST['stream_buffer_size'] ?? 20),  // 20ms default (milliseconds!)
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
			$array['voice_secretaries'][0]['fallback_action'] = $form_data['fallback_action'] ?: 'ticket';
			$array['voice_secretaries'][0]['handoff_queue_id'] = $form_data['handoff_queue_id'] ?: null;
			$array['voice_secretaries'][0]['fallback_user_id'] = $form_data['fallback_user_id'] ?: null;
			$array['voice_secretaries'][0]['fallback_priority'] = $form_data['fallback_priority'] ?: 'medium';
			$array['voice_secretaries'][0]['fallback_notify_enabled'] = $form_data['fallback_notify_enabled'];
			$array['voice_secretaries'][0]['omniplay_company_id'] = $form_data['omniplay_company_id'] ?: null;
			// Audio Configuration
			$array['voice_secretaries'][0]['audio_warmup_chunks'] = $form_data['audio_warmup_chunks'] ?: 15;
			$array['voice_secretaries'][0]['audio_warmup_ms'] = $form_data['audio_warmup_ms'] ?: 400;
			$array['voice_secretaries'][0]['audio_adaptive_warmup'] = $form_data['audio_adaptive_warmup'];
			$array['voice_secretaries'][0]['jitter_buffer_min'] = $form_data['jitter_buffer_min'] ?: 100;
			$array['voice_secretaries'][0]['jitter_buffer_max'] = $form_data['jitter_buffer_max'] ?: 300;
			$array['voice_secretaries'][0]['jitter_buffer_step'] = $form_data['jitter_buffer_step'] ?: 40;
			$array['voice_secretaries'][0]['stream_buffer_size'] = $form_data['stream_buffer_size'] ?: 20;  // 20ms default
			
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

//get extensions, ring groups and call center queues for transfer dropdown
	// Extensions - com nome para exibição
	$sql = "SELECT extension, effective_caller_id_name, description FROM v_extensions WHERE domain_uuid = :domain_uuid AND enabled = 'true' ORDER BY CAST(extension AS INTEGER)";
	$parameters['domain_uuid'] = $domain_uuid;
	$extensions = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);
	
	// Ring Groups - com nome
	$sql = "SELECT ring_group_extension, ring_group_name, ring_group_description FROM v_ring_groups WHERE domain_uuid = :domain_uuid AND ring_group_enabled = 'true' ORDER BY CAST(ring_group_extension AS INTEGER)";
	$parameters['domain_uuid'] = $domain_uuid;
	$ring_groups = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);
	
	// Call Center Queues - com nome
	$sql = "SELECT queue_extension, queue_name, queue_description FROM v_call_center_queues WHERE domain_uuid = :domain_uuid AND queue_enabled = 'true' ORDER BY CAST(queue_extension AS INTEGER)";
	$parameters['domain_uuid'] = $domain_uuid;
	$queues = $database->select($sql, $parameters, 'all') ?: [];
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
	echo "		<div style='background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px 12px; margin-top: 10px; border-radius: 4px; font-size: 0.9em;'>\n";
	echo "			<b style='color: #856404;'>💡 Dica:</b> Estas configurações são para <b>handoff GENÉRICO</b> (quando o cliente diz \"quero falar com alguém\").<br/>\n";
	echo "			<span style='color: #666;'>Para transferir para departamentos específicos (Vendas, Financeiro, Suporte), use a aba <b>\"Regras\"</b>.</span>\n";
	echo "		</div>\n";
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
	$current_transfer_ext = $data['transfer_extension'] ?? '200';
	echo "		<select class='formfld' name='transfer_extension' id='transfer_extension' style='width: 350px;'>\n";
	echo "			<option value=''>".($text['option-select'] ?? '-- Selecione --')."</option>\n";
	
	// Ramais
	if (!empty($extensions)) {
		echo "			<optgroup label='📞 Ramais'>\n";
		foreach ($extensions as $ext) {
			$ext_num = $ext['extension'];
			$ext_name = $ext['effective_caller_id_name'] ?: $ext['description'] ?: '';
			$display = $ext_name ? $ext_num . ' - ' . $ext_name : $ext_num;
			$selected = ($current_transfer_ext === $ext_num) ? 'selected' : '';
			echo "				<option value='".escape($ext_num)."' ".$selected.">".escape($display)."</option>\n";
		}
		echo "			</optgroup>\n";
	}
	
	// Ring Groups
	if (!empty($ring_groups)) {
		echo "			<optgroup label='🔔 Ring Groups'>\n";
		foreach ($ring_groups as $rg) {
			$rg_ext = $rg['ring_group_extension'];
			$rg_name = $rg['ring_group_name'] ?: $rg['ring_group_description'] ?: '';
			$display = $rg_name ? $rg_ext . ' - ' . $rg_name : $rg_ext;
			$selected = ($current_transfer_ext === $rg_ext) ? 'selected' : '';
			echo "				<option value='".escape($rg_ext)."' ".$selected.">".escape($display)."</option>\n";
		}
		echo "			</optgroup>\n";
	}
	
	// Call Center Queues
	if (!empty($queues)) {
		echo "			<optgroup label='📋 Filas de Call Center'>\n";
		foreach ($queues as $q) {
			$q_ext = $q['queue_extension'];
			$q_name = $q['queue_name'] ?: $q['queue_description'] ?: '';
			$display = $q_name ? $q_ext . ' - ' . $q_name : $q_ext;
			$selected = ($current_transfer_ext === $q_ext) ? 'selected' : '';
			echo "				<option value='".escape($q_ext)."' ".$selected.">".escape($display)."</option>\n";
		}
		echo "			</optgroup>\n";
	}
	
	echo "		</select>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>⚠️ Use a RECEPÇÃO ou fila GERAL</b> - Este é o ramal para handoff genérico (cliente diz 'quero falar com alguém'). "
		. "Para departamentos específicos (Vendas, Financeiro), use as <b>Regras de Transferência</b>."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-handoff_timeout'] ?? 'Handoff Timeout')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='handoff_timeout' min='5' max='120' value='".intval($data['handoff_timeout'] ?? 30)."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>segundos</span>\n";
	echo "		<br /><span class='vtable-hint'>Tempo aguardando atendente antes de criar callback/ticket. <b>Padrão:</b> 30s</span>\n";
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
	echo "		<input class='formfld' type='number' name='max_turns' min='1' max='100' value='".escape($data['max_turns'] ?? 20)."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>turnos</span>\n";
	echo "		<br /><span class='vtable-hint'>Apos X turnos, oferece transferencia para humano. <b>Padrao:</b> 20</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Handoff Keywords
	echo "<tr class='handoff-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-handoff_keywords'] ?? 'Handoff Keywords')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$keywords = $data['handoff_keywords'] ?? 'atendente,humano,pessoa,operador';
	echo "		<input class='formfld' type='text' name='handoff_keywords' maxlength='500' value='".escape($keywords)."' style='width: 100%;' placeholder='atendente,humano,pessoa,operador'>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>⚠️ NÃO inclua nomes de departamentos!</b> Use apenas termos genéricos como: <code>atendente, humano, pessoa, operador, recepcionista</code>. "
		. "Nomes de departamentos (vendas, financeiro, suporte) devem estar nas <b>Regras de Transferência</b>."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// =============================
	// Fallback Configuration Section
	// =============================
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #fff3cd; border-bottom: 1px solid #ffc107;'>\n";
	echo "		<b>".($text['header-fallback'] ?? '🔄 Fallback Configuration')."</b>\n";
	echo "		<span style='font-size: 0.85em; color: #856404; margin-left: 10px;'>"
		. "O que fazer quando a transferência falha ou ninguém atende"
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback Enabled
	echo "<tr class='handoff-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_enabled'] ?? 'Enable Fallback')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$fallback_enabled = (!isset($data['fallback_ticket_enabled']) || $data['fallback_ticket_enabled'] == 'true' || $data['fallback_ticket_enabled'] === true);
	echo "		<input type='checkbox' name='fallback_ticket_enabled' id='fallback_ticket_enabled' ".($fallback_enabled ? 'checked' : '')." onchange='toggleFallbackOptions()'>\n";
	echo "		<label for='fallback_ticket_enabled'>".($text['label-enabled'] ?? 'Enabled')."</label>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "Quando ativado, executa uma ação de fallback se a transferência falhar ou ninguém atender."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback Action
	echo "<tr class='handoff-option fallback-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_action'] ?? 'Fallback Action')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$fallback_action = $data['fallback_action'] ?? 'ticket';
	echo "		<select class='formfld' name='fallback_action' style='width: 250px;'>\n";
	echo "			<option value='ticket' ".($fallback_action === 'ticket' ? 'selected' : '').">📋 Criar Ticket Pendente</option>\n";
	echo "			<option value='callback' ".($fallback_action === 'callback' ? 'selected' : '').">📞 Agendar Callback</option>\n";
	echo "			<option value='voicemail' ".($fallback_action === 'voicemail' ? 'selected' : '').">📧 Enviar para Voicemail</option>\n";
	echo "			<option value='none' ".($fallback_action === 'none' ? 'selected' : '').">❌ Nenhuma Ação (Desligar)</option>\n";
	echo "		</select>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>Ticket:</b> Cria ticket no OmniPlay com transcrição da conversa<br/>"
		. "<b>Callback:</b> Agenda retorno de ligação para o cliente<br/>"
		. "<b>Voicemail:</b> Permite cliente deixar mensagem de voz"
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback Queue (for ticket/callback assignment)
	echo "<tr class='handoff-option fallback-option'>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_queue'] ?? 'Destination Queue')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	
	if ($omniplay_configured && !empty($omniplay_queues)) {
		// Dynamic dropdown from OmniPlay
		echo "		<select class='formfld' name='handoff_queue_id' style='width: 250px;' required>\n";
		echo "			<option value=''>-- Selecione uma fila --</option>\n";
		foreach ($omniplay_queues as $queue) {
			$selected = ($data['handoff_queue_id'] == $queue['id']) ? 'selected' : '';
			echo "			<option value='".escape($queue['id'])."' {$selected}>".escape($queue['name'])." (ID: ".escape($queue['id']).")</option>\n";
		}
		echo "		</select>\n";
		echo "		<a href='omniplay_settings.php?action=sync' style='margin-left: 10px; font-size: 0.85em;'>🔄 Atualizar lista</a>\n";
	} else {
		// Fallback to manual input
		echo "		<input class='formfld' type='number' name='handoff_queue_id' min='1' max='999' value='".escape($data['handoff_queue_id'] ?? '')."' placeholder='Ex: 1' style='width: 80px;' required>\n";
		echo "		<span style='margin-left: 10px; color: #666;'>ID da fila no OmniPlay</span>\n";
		if (!$omniplay_configured) {
			echo "		<br/><span style='color: #ff6b00; font-size: 0.85em;'>💡 <a href='omniplay_settings.php'>Configure a integração OmniPlay</a> para selecionar filas automaticamente</span>\n";
		}
	}
	
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>⚠️ OBRIGATÓRIO:</b> Defina qual fila do OmniPlay receberá os tickets/callbacks criados."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback User (optional - for direct assignment)
	echo "<tr class='handoff-option fallback-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_user'] ?? 'Assigned User')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	
	if ($omniplay_configured && !empty($omniplay_users)) {
		// Dynamic dropdown from OmniPlay
		echo "		<select class='formfld' name='fallback_user_id' style='width: 250px;'>\n";
		echo "			<option value=''>-- Qualquer atendente da fila --</option>\n";
		foreach ($omniplay_users as $user) {
			$selected = ($data['fallback_user_id'] == $user['id']) ? 'selected' : '';
			$online_status = !empty($user['online']) ? '🟢' : '⚪';
			echo "			<option value='".escape($user['id'])."' {$selected}>{$online_status} ".escape($user['name'])." (ID: ".escape($user['id']).")</option>\n";
		}
		echo "		</select>\n";
	} else {
		// Fallback to manual input
		echo "		<input class='formfld' type='number' name='fallback_user_id' min='1' max='99999' value='".escape($data['fallback_user_id'] ?? '')."' placeholder='(Opcional)' style='width: 80px;'>\n";
		echo "		<span style='margin-left: 10px; color: #666;'>ID do usuário no OmniPlay</span>\n";
	}
	
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>Opcional:</b> Atribuir diretamente a um usuário específico em vez de deixar na fila.<br/>"
		. "Deixe em branco para que qualquer atendente da fila possa pegar o ticket."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback Priority
	echo "<tr class='handoff-option fallback-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_priority'] ?? 'Ticket Priority')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$fallback_priority = $data['fallback_priority'] ?? 'medium';
	echo "		<select class='formfld' name='fallback_priority' style='width: 150px;'>\n";
	echo "			<option value='low' ".($fallback_priority === 'low' ? 'selected' : '').">🟢 Baixa</option>\n";
	echo "			<option value='medium' ".($fallback_priority === 'medium' ? 'selected' : '').">🟡 Média</option>\n";
	echo "			<option value='high' ".($fallback_priority === 'high' ? 'selected' : '').">🟠 Alta</option>\n";
	echo "			<option value='urgent' ".($fallback_priority === 'urgent' ? 'selected' : '').">🔴 Urgente</option>\n";
	echo "		</select>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "Prioridade do ticket criado no OmniPlay."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback Notification
	echo "<tr class='handoff-option fallback-option'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_notify'] ?? 'Notify Client')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$fallback_notify = (!isset($data['fallback_notify_enabled']) || $data['fallback_notify_enabled'] == 'true' || $data['fallback_notify_enabled'] === true);
	echo "		<input type='checkbox' name='fallback_notify_enabled' id='fallback_notify_enabled' ".($fallback_notify ? 'checked' : '').">\n";
	echo "		<label for='fallback_notify_enabled'>Enviar notificação ao cliente</label>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "Envia WhatsApp ou SMS informando que o ticket foi criado e prazo de retorno."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// =============================
	// Audio Configuration Section
	// =============================
	echo "<tr>\n";
	echo "	<td colspan='2' style='padding: 12px 10px; background: #f8f9fa; border-bottom: 1px solid #dee2e6;'>\n";
	echo "		<b>".($text['header-audio_config'] ?? '🔊 Audio Configuration')."</b>\n";
	echo "		<span style='font-size: 0.85em; color: #666; margin-left: 10px;'>"
		. ($text['header-audio_config_desc'] ?? 'Buffer and jitter settings to prevent choppy audio')
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Audio Warmup Chunks
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-audio_warmup_chunks'] ?? 'Warmup Chunks')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='audio_warmup_chunks' min='5' max='50' value='".intval($data['audio_warmup_chunks'] ?? 15)."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>chunks (x20ms = ".intval(($data['audio_warmup_chunks'] ?? 15) * 20)."ms)</span>\n";
	echo "		<br /><span class='vtable-hint'>".($text['description-audio_warmup_chunks'] ?? 'Number of 20ms audio chunks to buffer before playback. Higher = more stable, but adds latency.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Audio Warmup MS
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-audio_warmup_ms'] ?? 'Warmup Buffer (ms)')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='audio_warmup_ms' min='100' max='1000' step='50' value='".intval($data['audio_warmup_ms'] ?? 400)."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>ms</span>\n";
	echo "		<br /><span class='vtable-hint'>".($text['description-audio_warmup_ms'] ?? 'Resampler warmup buffer in milliseconds. Recommended: 300-500ms.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Adaptive Warmup
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-audio_adaptive'] ?? 'Adaptive Warmup')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$adaptive_enabled = (!isset($data['audio_adaptive_warmup']) || $data['audio_adaptive_warmup'] == 'true' || $data['audio_adaptive_warmup'] === true);
	echo "		<input type='checkbox' name='audio_adaptive_warmup' id='audio_adaptive_warmup' ".($adaptive_enabled ? 'checked' : '').">\n";
	echo "		<label for='audio_adaptive_warmup'>".($text['label-enabled'] ?? 'Enabled')."</label>\n";
	echo "		<br /><span class='vtable-hint'>".($text['description-audio_adaptive'] ?? 'Automatically adjust warmup based on network conditions.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Jitter Buffer Settings (FreeSWITCH)
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-jitter_buffer'] ?? 'Jitter Buffer (FS)')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<div style='display: flex; gap: 10px; align-items: center;'>\n";
	echo "			<label>Min:</label>\n";
	echo "			<input class='formfld' type='number' name='jitter_buffer_min' min='20' max='500' value='".intval($data['jitter_buffer_min'] ?? 100)."' style='width: 70px;'>ms\n";
	echo "			<label style='margin-left: 10px;'>Max:</label>\n";
	echo "			<input class='formfld' type='number' name='jitter_buffer_max' min='100' max='1000' value='".intval($data['jitter_buffer_max'] ?? 300)."' style='width: 70px;'>ms\n";
	echo "			<label style='margin-left: 10px;'>Step:</label>\n";
	echo "			<input class='formfld' type='number' name='jitter_buffer_step' min='10' max='100' value='".intval($data['jitter_buffer_step'] ?? 40)."' style='width: 60px;'>ms\n";
	echo "		</div>\n";
	echo "		<br /><span class='vtable-hint'>".($text['description-jitter_buffer'] ?? 'FreeSWITCH jitter buffer settings: min:max:step. Default: 100:300:40')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Stream Buffer Size
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-stream_buffer'] ?? 'Stream Buffer Size')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='stream_buffer_size' min='20' max='100' step='20' value='".intval($data['stream_buffer_size'] ?? 20)."' style='width: 80px;'>\n";
	echo "		<span style='margin-left: 5px;'>ms</span>\n";
	echo "		<br /><span class='vtable-hint'>".($text['description-stream_buffer'] ?? 'mod_audio_stream buffer size in MILLISECONDS. 20ms = default (recommended). Higher = more stable but higher latency.')."</span>\n";
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
	// Also update fallback options visibility
	if (enabled) {
		toggleFallbackOptions();
	}
}

function toggleFallbackOptions() {
	var enabled = document.getElementById('fallback_ticket_enabled')?.checked;
	var rows = document.querySelectorAll('.fallback-option');
	rows.forEach(function(row) {
		row.style.display = enabled ? '' : 'none';
	});
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
	toggleRealtimeProvider();
	toggleHandoffOptions();
	toggleFallbackOptions();
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