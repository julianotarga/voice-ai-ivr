<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - AI Providers List
	Lists configured AI providers for the domain.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";
	require_once "resources/check_auth.php";

//check permissions
	if (permission_exists('voice_secretary_view')) {
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

//include class
	require_once "resources/classes/voice_ai_provider.php";

//get posted data
	if (!empty($_POST['providers']) && is_array($_POST['providers'])) {
		$action = $_POST['action'] ?? '';
		$providers_list = $_POST['providers'];
	}

//process the http post data by action
	if (!empty($action) && !empty($providers_list) && is_array($providers_list) && @sizeof($providers_list) != 0) {

		//validate the token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: providers.php');
			exit;
		}

		switch ($action) {
			case 'toggle':
				if (permission_exists('voice_secretary_edit')) {
					$provider_obj = new voice_ai_provider;
					$provider_obj->toggle($providers_list, $domain_uuid);
				}
				break;
			case 'delete':
				if (permission_exists('voice_secretary_delete')) {
					$provider_obj = new voice_ai_provider;
					$provider_obj->delete($providers_list, $domain_uuid);
				}
				break;
		}

		header('Location: providers.php');
		exit;
	}

//get providers
	$provider_obj = new voice_ai_provider;
	$providers = $provider_obj->get_list($domain_uuid) ?: [];
	$num_rows = count($providers);

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//set the title
	$document['title'] = $text['title-voice_ai_providers'] ?? 'AI Providers';

//include the header
	require_once "resources/header.php";

//include tab navigation
	$current_page = 'providers';
	require_once "resources/nav_tabs.php";

//show the content
	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".($text['title-voice_ai_providers'] ?? 'AI Providers')."</b><div class='count'>".number_format($num_rows)."</div></div>\n";
	echo "	<div class='actions'>\n";
	if (permission_exists('voice_secretary_add')) {
		echo button::create(['type'=>'button','label'=>$text['button-add'],'icon'=>$_SESSION['theme']['button_icon_add'],'id'=>'btn_add','link'=>'providers_edit.php']);
	}
	if (permission_exists('voice_secretary_edit') && $providers) {
		echo button::create(['type'=>'button','label'=>$text['button-toggle'],'icon'=>$_SESSION['theme']['button_icon_toggle'],'id'=>'btn_toggle','name'=>'btn_toggle','style'=>'display: none;','onclick'=>"modal_open('modal-toggle','btn_toggle');"]);
	}
	if (permission_exists('voice_secretary_delete') && $providers) {
		echo button::create(['type'=>'button','label'=>$text['button-delete'],'icon'=>$_SESSION['theme']['button_icon_delete'],'id'=>'btn_delete','name'=>'btn_delete','style'=>'display: none;','onclick'=>"modal_open('modal-delete','btn_delete');"]);
	}
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	if (permission_exists('voice_secretary_edit') && $providers) {
		echo modal::create(['id'=>'modal-toggle','type'=>'toggle','actions'=>button::create(['type'=>'button','label'=>$text['button-continue'],'icon'=>'check','id'=>'btn_toggle','style'=>'float: right; margin-left: 15px;','collapse'=>'never','onclick'=>"modal_close(); list_action_set('toggle'); list_form_submit('form_list');"])]);
	}
	if (permission_exists('voice_secretary_delete') && $providers) {
		echo modal::create(['id'=>'modal-delete','type'=>'delete','actions'=>button::create(['type'=>'button','label'=>$text['button-continue'],'icon'=>'check','id'=>'btn_delete','style'=>'float: right; margin-left: 15px;','collapse'=>'never','onclick'=>"modal_close(); list_action_set('delete'); list_form_submit('form_list');"])]);
	}

	echo ($text['description-voice_ai_providers'] ?? 'Configure AI providers for speech recognition, text-to-speech, language models, and embeddings.')."\n";
	echo "<br /><br />\n";

	echo "<form id='form_list' method='post'>\n";
	echo "<input type='hidden' id='action' name='action' value=''>\n";

	echo "<div class='card'>\n";
	echo "<table class='list'>\n";
	echo "<tr class='list-header'>\n";
	if (permission_exists('voice_secretary_edit') || permission_exists('voice_secretary_delete')) {
		echo "	<th class='checkbox'>\n";
		echo "		<input type='checkbox' id='checkbox_all' name='checkbox_all' onclick='list_all_toggle(); checkbox_on_change(this);' ".(empty($providers) ? "style='visibility: hidden;'" : null).">\n";
		echo "	</th>\n";
	}
	echo "<th>".($text['label-provider_type'] ?? 'Type')."</th>\n";
	echo "<th>".($text['label-provider_name'] ?? 'Provider')."</th>\n";
	echo "<th class='center'>".($text['label-priority'] ?? 'Priority')."</th>\n";
	echo "<th class='center'>".($text['label-default'] ?? 'Default')."</th>\n";
	echo "<th class='center'>".($text['label-enabled'] ?? 'Enabled')."</th>\n";
	if (permission_exists('voice_secretary_edit') && $_SESSION['theme']['list_row_edit_button']['boolean'] ?? false) {
		echo "	<td class='action-button'>&nbsp;</td>\n";
	}
	echo "</tr>\n";

	$type_labels = [
		'stt' => 'Speech-to-Text',
		'tts' => 'Text-to-Speech',
		'llm' => 'LLM',
		'embeddings' => 'Embeddings',
		'realtime' => 'Realtime',
	];

	if (is_array($providers) && @sizeof($providers) != 0) {
		$x = 0;
		foreach($providers as $row) {
			$list_row_url = permission_exists('voice_secretary_edit') ? "providers_edit.php?id=".urlencode($row['voice_ai_provider_uuid']) : '';
			echo "<tr class='list-row' href='".$list_row_url."'>\n";
			if (permission_exists('voice_secretary_edit') || permission_exists('voice_secretary_delete')) {
				echo "	<td class='checkbox'>\n";
				echo "		<input type='checkbox' name='providers[$x][checked]' id='checkbox_".$x."' value='true' onclick=\"checkbox_on_change(this); if (!this.checked) { document.getElementById('checkbox_all').checked = false; }\">\n";
				echo "		<input type='hidden' name='providers[$x][uuid]' value='".escape($row['voice_ai_provider_uuid'])."' />\n";
				echo "	</td>\n";
			}
			echo "	<td>".($type_labels[$row['provider_type'] ?? ''] ?? escape($row['provider_type']))."</td>\n";
			echo "	<td>";
			if (permission_exists('voice_secretary_edit')) {
				echo "<a href='".$list_row_url."' title=\"".$text['button-edit']."\">".escape($row['provider_name'])."</a>";
			}
			else {
				echo escape($row['provider_name']);
			}
			echo "	</td>\n";
			echo "	<td class='center'>".intval($row['priority'] ?? 0)."</td>\n";
			echo "	<td class='center'>".($row['is_default'] ? $text['label-true'] : $text['label-false'])."</td>\n";
			
			if (permission_exists('voice_secretary_edit')) {
				echo "	<td class='no-link center'>";
				$enabled = ($row['is_enabled'] ?? true) ? 'true' : 'false';
				echo button::create(['type'=>'submit','class'=>'link','label'=>$text['label-'.$enabled],'title'=>$text['button-toggle'],'onclick'=>"list_self_check('checkbox_".$x."'); list_action_set('toggle'); list_form_submit('form_list')"]);
			}
			else {
				echo "	<td class='center'>";
				echo $text['label-'.(($row['is_enabled'] ?? true) ? 'true' : 'false')];
			}
			echo "	</td>\n";
			if (permission_exists('voice_secretary_edit') && $_SESSION['theme']['list_row_edit_button']['boolean'] ?? false) {
				echo "	<td class='action-button'>";
				echo button::create(['type'=>'button','title'=>$text['button-edit'],'icon'=>$_SESSION['theme']['button_icon_edit'],'link'=>$list_row_url]);
				echo "	</td>\n";
			}
			echo "</tr>\n";
			$x++;
		}
	}
	else {
		echo "<tr><td colspan='7' class='no-results-found'>".($text['message-no_records'] ?? 'No records found.')."</td></tr>\n";
	}

	echo "</table>\n";
	echo "</div>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

//include the footer
	require_once "resources/footer.php";

?>
