<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Virtual Secretary with AI
	List of secretaries configured for the domain.
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

//include class
	require_once "resources/classes/voice_secretary.php";

//get variables
	$domain_uuid = $_SESSION['domain_uuid'] ?? null;
	if (!$domain_uuid) {
		echo "access denied";
		exit;
	}

//get posted data
	if (!empty($_POST['secretaries']) && is_array($_POST['secretaries'])) {
		$action = $_POST['action'] ?? '';
		$secretaries = $_POST['secretaries'];
	}

//process the http post data by action
	if (!empty($action) && !empty($secretaries) && is_array($secretaries) && @sizeof($secretaries) != 0) {

		//validate the token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: secretary.php');
			exit;
		}

		switch ($action) {
			case 'toggle':
				if (permission_exists('voice_secretary_edit')) {
					$secretary = new voice_secretary;
					$secretary->toggle($secretaries, $domain_uuid);
				}
				break;
			case 'delete':
				if (permission_exists('voice_secretary_delete')) {
					$secretary = new voice_secretary;
					$secretary->delete($secretaries, $domain_uuid);
				}
				break;
		}

		header('Location: secretary.php');
		exit;
	}

//get data
	$secretary = new voice_secretary;
	$secretaries = $secretary->get_list($domain_uuid) ?: [];
	$num_rows = count($secretaries);

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = $text['title-voice_secretaries'] ?? 'Voice Secretaries';
	require_once "resources/header.php";

//include tab navigation
	$current_page = 'secretaries';
	require_once "resources/nav_tabs.php";

//show the content
	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$text['title-voice_secretaries']."</b><div class='count'>".number_format($num_rows)."</div></div>\n";
	echo "	<div class='actions'>\n";
	if (permission_exists('voice_secretary_add')) {
		echo button::create(['type'=>'button','label'=>$text['button-add'],'icon'=>$_SESSION['theme']['button_icon_add'],'id'=>'btn_add','link'=>'secretary_edit.php']);
	}
	if (permission_exists('voice_secretary_edit') && $secretaries) {
		echo button::create(['type'=>'button','label'=>$text['button-toggle'],'icon'=>$_SESSION['theme']['button_icon_toggle'],'id'=>'btn_toggle','name'=>'btn_toggle','style'=>'display: none;','onclick'=>"modal_open('modal-toggle','btn_toggle');"]);
	}
	if (permission_exists('voice_secretary_delete') && $secretaries) {
		echo button::create(['type'=>'button','label'=>$text['button-delete'],'icon'=>$_SESSION['theme']['button_icon_delete'],'id'=>'btn_delete','name'=>'btn_delete','style'=>'display: none;','onclick'=>"modal_open('modal-delete','btn_delete');"]);
	}
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	if (permission_exists('voice_secretary_edit') && $secretaries) {
		echo modal::create(['id'=>'modal-toggle','type'=>'toggle','actions'=>button::create(['type'=>'button','label'=>$text['button-continue'],'icon'=>'check','id'=>'btn_toggle','style'=>'float: right; margin-left: 15px;','collapse'=>'never','onclick'=>"modal_close(); list_action_set('toggle'); list_form_submit('form_list');"])]);
	}
	if (permission_exists('voice_secretary_delete') && $secretaries) {
		echo modal::create(['id'=>'modal-delete','type'=>'delete','actions'=>button::create(['type'=>'button','label'=>$text['button-continue'],'icon'=>'check','id'=>'btn_delete','style'=>'float: right; margin-left: 15px;','collapse'=>'never','onclick'=>"modal_close(); list_action_set('delete'); list_form_submit('form_list');"])]);
	}

	echo $text['description-voice_secretaries']."\n";
	echo "<br /><br />\n";

	echo "<form id='form_list' method='post'>\n";
	echo "<input type='hidden' id='action' name='action' value=''>\n";

	echo "<div class='card'>\n";
	echo "<table class='list'>\n";
	echo "<tr class='list-header'>\n";
	if (permission_exists('voice_secretary_edit') || permission_exists('voice_secretary_delete')) {
		echo "	<th class='checkbox'>\n";
		echo "		<input type='checkbox' id='checkbox_all' name='checkbox_all' onclick='list_all_toggle(); checkbox_on_change(this);' ".(empty($secretaries) ? "style='visibility: hidden;'" : null).">\n";
		echo "	</th>\n";
	}
	echo th_order_by('secretary_name', $text['label-secretary_name'], $order_by ?? '', $order ?? '');
	echo th_order_by('company_name', $text['label-company_name'], $order_by ?? '', $order ?? '', '', "class='hide-sm-dn'");
	echo th_order_by('extension', $text['label-extension'], $order_by ?? '', $order ?? '');
	echo th_order_by('processing_mode', $text['label-processing_mode'], $order_by ?? '', $order ?? '');
	echo th_order_by('enabled', $text['label-enabled'], $order_by ?? '', $order ?? '', '', "class='center'");
	echo th_order_by('insert_date', $text['label-created'], $order_by ?? '', $order ?? '', '', "class='hide-sm-dn'");
	if (permission_exists('voice_secretary_edit') && $_SESSION['theme']['list_row_edit_button']['boolean'] ?? false) {
		echo "	<td class='action-button'>&nbsp;</td>\n";
	}
	echo "</tr>\n";

	if (is_array($secretaries) && @sizeof($secretaries) != 0) {
		$x = 0;
		foreach($secretaries as $row) {
			$list_row_url = permission_exists('voice_secretary_edit') ? "secretary_edit.php?id=".urlencode($row['voice_secretary_uuid']) : '';
			echo "<tr class='list-row' href='".$list_row_url."'>\n";
			if (permission_exists('voice_secretary_edit') || permission_exists('voice_secretary_delete')) {
				echo "	<td class='checkbox'>\n";
				echo "		<input type='checkbox' name='secretaries[$x][checked]' id='checkbox_".$x."' value='true' onclick=\"checkbox_on_change(this); if (!this.checked) { document.getElementById('checkbox_all').checked = false; }\">\n";
				echo "		<input type='hidden' name='secretaries[$x][uuid]' value='".escape($row['voice_secretary_uuid'])."' />\n";
				echo "	</td>\n";
			}
			echo "	<td>";
			if (permission_exists('voice_secretary_edit')) {
				echo "<a href='".$list_row_url."' title=\"".$text['button-edit']."\">".escape($row['secretary_name'])."</a>";
			}
			else {
				echo escape($row['secretary_name']);
			}
			echo "	</td>\n";
			echo "	<td class='hide-sm-dn'>".escape($row['company_name'] ?? '')."&nbsp;</td>\n";
			echo "	<td>".escape($row['extension'] ?? '')."&nbsp;</td>\n";
			
			// Processing mode with visual indicator
			$mode = $row['processing_mode'] ?? 'turn_based';
			$mode_labels = [
				'turn_based' => '<span class="badge badge-info">Turn-based</span>',
				'realtime' => '<span class="badge badge-success">Realtime</span>',
				'auto' => '<span class="badge badge-secondary">Auto</span>'
			];
			echo "	<td>".($mode_labels[$mode] ?? escape($mode))."</td>\n";
			
			if (permission_exists('voice_secretary_edit')) {
				echo "	<td class='no-link center'>";
				$enabled = $row['enabled'] ?? 'true';
				echo button::create(['type'=>'submit','class'=>'link','label'=>$text['label-'.$enabled],'title'=>$text['button-toggle'],'onclick'=>"list_self_check('checkbox_".$x."'); list_action_set('toggle'); list_form_submit('form_list')"]);
			}
			else {
				echo "	<td class='center'>";
				echo $text['label-'.($row['enabled'] ?? 'true')];
			}
			echo "	</td>\n";
			echo "	<td class='hide-sm-dn'>".(!empty($row['insert_date']) ? date('d/m/Y H:i', strtotime($row['insert_date'])) : '')."</td>\n";
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
