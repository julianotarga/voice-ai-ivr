<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - AI Provider Edit
	Create or edit an AI provider.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";
	require_once "resources/check_auth.php";

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

//get domain_uuid from session
	$domain_uuid = $_SESSION['domain_uuid'] ?? null;
	if (!$domain_uuid) {
		echo "access denied";
		exit;
	}

//include classes
	require_once "resources/classes/voice_ai_provider.php";

//set the action
	$provider_obj = new voice_ai_provider();
	$action = 'add';
	$data = [];

//check if editing existing
	if (isset($_GET['id']) && is_uuid($_GET['id'])) {
		$action = 'edit';
		$provider_uuid = $_GET['id'];
		$data = $provider_obj->get($provider_uuid, $domain_uuid);
		
		if (!$data) {
			message::add($text['message-invalid_id'] ?? 'Invalid ID', 'negative');
			header('Location: providers.php');
			exit;
		}
		
		//decode config
		$data['config'] = json_decode($data['config'] ?? '{}', true) ?: [];
	}

//process form submission
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {

		//validate the token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: providers.php');
			exit;
		}

		//build config from form
		$config = [];
		if (isset($_POST['config']) && is_array($_POST['config'])) {
			$config = $_POST['config'];
		}
		
		$form_data = [
			'provider_type' => $_POST['provider_type'] ?? '',
			'provider_name' => $_POST['provider_name'] ?? '',
			'config' => $config,
			'is_enabled' => isset($_POST['is_enabled']),
			'is_default' => isset($_POST['is_default']),
			'priority' => intval($_POST['priority'] ?? 10),
		];
		
		try {
			if ($action === 'add') {
				$provider_obj->create($form_data, $domain_uuid);
				message::add($text['message-add']);
			} else {
				$provider_obj->update($provider_uuid, $form_data, $domain_uuid);
				message::add($text['message-update']);
			}
			header('Location: providers.php');
			exit;
		} catch (Exception $e) {
			message::add($e->getMessage(), 'negative');
		}
	}

//get provider options
	$all_providers = voice_ai_provider::PROVIDERS;

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = ($action === 'add') ? ($text['title-add_provider'] ?? 'Add Provider') : ($text['title-edit_provider'] ?? 'Edit Provider');
	require_once "resources/header.php";

//show the content
	echo "<form method='post' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'button','label'=>$text['button-back'],'icon'=>$_SESSION['theme']['button_icon_back'],'id'=>'btn_back','link'=>'providers.php']);
	echo button::create(['type'=>'submit','name'=>'submit','label'=>$text['button-save'],'icon'=>$_SESSION['theme']['button_icon_save'],'id'=>'btn_save','style'=>'margin-left: 15px;']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-provider_edit'] ?? 'Configure an AI provider.')."\n";
	echo "<br /><br />\n";

	echo "<div class='card'>\n";
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	//Provider Type
	echo "<tr>\n";
	echo "	<td width='30%' class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-provider_type'] ?? 'Provider Type')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	if ($action === 'edit') {
		$type_labels = ['stt' => 'Speech-to-Text (STT)', 'tts' => 'Text-to-Speech (TTS)', 'llm' => 'Large Language Model (LLM)', 'embeddings' => 'Embeddings', 'realtime' => 'Realtime'];
		echo "		<span class='badge badge-info'>".($type_labels[$data['provider_type'] ?? ''] ?? escape($data['provider_type']))."</span>\n";
		echo "		<input type='hidden' name='provider_type' value='".escape($data['provider_type'])."'>\n";
	} else {
		echo "		<select name='provider_type' id='provider_type' class='formfld' required onchange='updateProviderOptions()'>\n";
		echo "			<option value=''>".($text['option-select'] ?? '-- Select --')."</option>\n";
		echo "			<option value='stt'>Speech-to-Text (STT)</option>\n";
		echo "			<option value='tts'>Text-to-Speech (TTS)</option>\n";
		echo "			<option value='llm'>Large Language Model (LLM)</option>\n";
		echo "			<option value='embeddings'>Embeddings</option>\n";
		echo "			<option value='realtime'>Realtime</option>\n";
		echo "		</select>\n";
	}
	echo "	</td>\n";
	echo "</tr>\n";

	//Provider Name
	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-provider_name'] ?? 'Provider')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	if ($action === 'edit') {
		echo "		<strong>".escape($data['provider_name'] ?? '')."</strong>\n";
		echo "		<input type='hidden' name='provider_name' value='".escape($data['provider_name'])."'>\n";
	} else {
		echo "		<select name='provider_name' id='provider_name' class='formfld' required onchange='updateConfigFields()'>\n";
		echo "			<option value=''>".($text['option-select'] ?? '-- Select --')."</option>\n";
		echo "		</select>\n";
	}
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";

	//Dynamic config fields container
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0' id='config_fields'>\n";
	
	if ($action === 'edit' && !empty($data['provider_name'])) {
		$fields = voice_ai_provider::get_config_fields($data['provider_name']);
		foreach ($fields as $field) {
			$value = $data['config'][$field['name']] ?? ($field['default'] ?? '');
			echo "<tr>\n";
			echo "	<td width='30%' class='vncell".(!empty($field['required']) ? 'req' : '')."' valign='top' align='left' nowrap='nowrap'>".escape($field['label'])."</td>\n";
			echo "	<td width='70%' class='vtable' align='left'>\n";
			if ($field['type'] === 'select' && isset($field['options'])) {
				echo "		<select name='config[".escape($field['name'])."]' class='formfld'>\n";
				foreach ($field['options'] as $opt) {
					$selected = ($value === $opt) ? 'selected' : '';
					echo "			<option value='".escape($opt)."' ".$selected.">".escape($opt)."</option>\n";
				}
				echo "		</select>\n";
			} elseif ($field['type'] === 'password') {
				echo "		<input type='password' name='config[".escape($field['name'])."]' class='formfld' value='".escape($value)."' autocomplete='new-password' ".(!empty($field['required']) ? 'required' : '').">\n";
			} else {
				echo "		<input type='text' name='config[".escape($field['name'])."]' class='formfld' value='".escape($value)."' ".(!empty($field['required']) ? 'required' : '').">\n";
			}
			echo "	</td>\n";
			echo "</tr>\n";
		}
	}
	
	echo "</table>\n";

	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	//Priority
	echo "<tr>\n";
	echo "	<td width='30%' class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-priority'] ?? 'Priority')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input type='number' name='priority' class='formfld' min='1' max='100' value='".intval($data['priority'] ?? 10)."'>\n";
	echo "		<br />".($text['description-priority'] ?? 'Lower number = higher priority.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	//Is Default
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-default'] ?? 'Default')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$is_default = (!empty($data['is_default'])) ? 'checked' : '';
	echo "		<input type='checkbox' name='is_default' ".$is_default.">\n";
	echo "		<span>".($text['description-default'] ?? 'Set as default provider for this type.')."\n</span>";
	echo "	</td>\n";
	echo "</tr>\n";

	//Enabled
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-enabled'] ?? 'Enabled')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$is_enabled = (!isset($data['is_enabled']) || $data['is_enabled']) ? 'checked' : '';
	echo "		<input type='checkbox' name='is_enabled' ".$is_enabled.">\n";
	echo "		<span>".($text['description-enabled'] ?? 'Enable this provider.')."\n</span>";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "</div>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

?>

<script>
const providers = <?php echo json_encode($all_providers); ?>;
const configFields = <?php echo json_encode([
	'whisper_local' => voice_ai_provider::get_config_fields('whisper_local'),
	'whisper_api' => voice_ai_provider::get_config_fields('whisper_api'),
	'azure_speech' => voice_ai_provider::get_config_fields('azure_speech'),
	'google_speech' => voice_ai_provider::get_config_fields('google_speech'),
	'aws_transcribe' => voice_ai_provider::get_config_fields('aws_transcribe'),
	'deepgram' => voice_ai_provider::get_config_fields('deepgram'),
	'piper_local' => voice_ai_provider::get_config_fields('piper_local'),
	'coqui_local' => voice_ai_provider::get_config_fields('coqui_local'),
	'openai_tts' => voice_ai_provider::get_config_fields('openai_tts'),
	'elevenlabs' => voice_ai_provider::get_config_fields('elevenlabs'),
	'azure_neural' => voice_ai_provider::get_config_fields('azure_neural'),
	'google_tts' => voice_ai_provider::get_config_fields('google_tts'),
	'aws_polly' => voice_ai_provider::get_config_fields('aws_polly'),
	'playht' => voice_ai_provider::get_config_fields('playht'),
	'openai' => voice_ai_provider::get_config_fields('openai'),
	'azure_openai' => voice_ai_provider::get_config_fields('azure_openai'),
	'anthropic' => voice_ai_provider::get_config_fields('anthropic'),
	'google_gemini' => voice_ai_provider::get_config_fields('google_gemini'),
	'groq' => voice_ai_provider::get_config_fields('groq'),
	'ollama_local' => voice_ai_provider::get_config_fields('ollama_local'),
	'lmstudio_local' => voice_ai_provider::get_config_fields('lmstudio_local'),
	'openai_embeddings' => voice_ai_provider::get_config_fields('openai_embeddings'),
	'azure_embeddings' => voice_ai_provider::get_config_fields('azure_embeddings'),
	'cohere' => voice_ai_provider::get_config_fields('cohere'),
	'voyage' => voice_ai_provider::get_config_fields('voyage'),
	'local_embeddings' => voice_ai_provider::get_config_fields('local_embeddings'),
	'openai_realtime' => voice_ai_provider::get_config_fields('openai_realtime'),
	'elevenlabs_conv' => voice_ai_provider::get_config_fields('elevenlabs_conv'),
	'gemini_live' => voice_ai_provider::get_config_fields('gemini_live'),
	'custom_pipeline' => voice_ai_provider::get_config_fields('custom_pipeline'),
]); ?>;

function updateProviderOptions() {
	const type = document.getElementById('provider_type').value;
	const select = document.getElementById('provider_name');
	
	select.innerHTML = '<option value=""><?php echo addslashes($text['option-select'] ?? '-- Select --'); ?></option>';
	
	if (type && providers[type]) {
		for (const [key, label] of Object.entries(providers[type])) {
			const option = document.createElement('option');
			option.value = key;
			option.textContent = label;
			select.appendChild(option);
		}
	}
	
	//clear config fields
	document.getElementById('config_fields').innerHTML = '';
}

function updateConfigFields() {
	const providerName = document.getElementById('provider_name').value;
	const container = document.getElementById('config_fields');
	
	container.innerHTML = '';
	
	if (providerName && configFields[providerName]) {
		configFields[providerName].forEach(field => {
			const tr = document.createElement('tr');
			const tdLabel = document.createElement('td');
			const tdInput = document.createElement('td');
			
			tdLabel.style.width = '30%';
			tdLabel.className = 'vncell' + (field.required ? 'req' : '');
			tdLabel.style.verticalAlign = 'top';
			tdLabel.textContent = field.label;
			
			tdInput.style.width = '70%';
			tdInput.className = 'vtable';
			
			let input;
			if (field.type === 'select' && field.options) {
				input = document.createElement('select');
				input.className = 'formfld';
				input.name = 'config[' + field.name + ']';
				field.options.forEach(opt => {
					const option = document.createElement('option');
					option.value = opt;
					option.textContent = opt;
					if (opt === field.default) option.selected = true;
					input.appendChild(option);
				});
			} else {
				input = document.createElement('input');
				input.type = field.type === 'password' ? 'password' : 'text';
				input.className = 'formfld';
				input.name = 'config[' + field.name + ']';
				if (field.default) input.value = field.default;
				if (field.required) input.required = true;
			}
			
			tdInput.appendChild(input);
			tr.appendChild(tdLabel);
			tr.appendChild(tdInput);
			container.appendChild(tr);
		});
	}
}
</script>

<?php

//include the footer
	require_once "resources/footer.php";

?>
