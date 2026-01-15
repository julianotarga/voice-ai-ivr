<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Transfer Rules List Page
	Lists transfer rules for voice AI.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";

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

//process delete action
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && permission_exists('voice_secretary_delete')) {
		$action = $_POST['action'] ?? '';
		$rules = $_POST['rules'] ?? [];
		
		if ($action === 'delete' && is_array($rules) && count($rules) > 0) {
			//validate token
			$token = new token;
			if (!$token->validate($_SERVER['PHP_SELF'])) {
				message::add($text['message-invalid_token'],'negative');
				header('Location: transfer_rules.php');
				exit;
			}
			
			$database = new database;
			foreach ($rules as $uuid) {
				if (is_uuid($uuid)) {
					$sql = "DELETE FROM v_voice_transfer_rules WHERE transfer_rule_uuid = :uuid AND domain_uuid = :domain_uuid";
					$parameters['uuid'] = $uuid;
					$parameters['domain_uuid'] = $domain_uuid;
					$database->execute($sql, $parameters);
					unset($parameters);
				}
			}
			message::add($text['message-delete']);
			header('Location: transfer_rules.php');
			exit;
		}
	}

//get transfer rules
	$database = new database;
	$sql = "SELECT r.transfer_rule_uuid, r.department_name, r.keywords, r.transfer_extension, 
			       r.transfer_message, r.priority, r.is_active, r.voice_secretary_uuid,
			       s.secretary_name 
			FROM v_voice_transfer_rules r
			LEFT JOIN v_voice_secretaries s ON s.voice_secretary_uuid = r.voice_secretary_uuid
			WHERE r.domain_uuid = :domain_uuid 
			ORDER BY r.priority ASC, r.department_name ASC";
	$parameters['domain_uuid'] = $domain_uuid;
	$rules = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = $text['title-transfer_rules'] ?? 'Transfer Rules';
	require_once "resources/header.php";

//show the content
	echo "<form id='form_list' method='post'>\n";
	echo "<input type='hidden' name='action' id='action' value=''>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	if (permission_exists('voice_secretary_add')) {
		echo button::create(['type'=>'button','label'=>$text['button-add'],'icon'=>$_SESSION['theme']['button_icon_add'],'id'=>'btn_add','link'=>'transfer_rules_edit.php']);
	}
	if (permission_exists('voice_secretary_delete') && is_array($rules) && count($rules) > 0) {
		echo button::create(['type'=>'button','label'=>$text['button-delete'],'icon'=>$_SESSION['theme']['button_icon_delete'],'id'=>'btn_delete','name'=>'btn_delete','style'=>'margin-left: 15px;','onclick'=>"if (confirm('".$text['confirm-delete']."')) { document.getElementById('action').value = 'delete'; document.getElementById('form_list').submit(); }"]);
	}
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-transfer_rules'] ?? 'Configure transfer rules based on keywords.')."\n";
	echo "<br /><br />\n";

	echo "<table class='list'>\n";
	echo "<tr class='list-header'>\n";
	if (permission_exists('voice_secretary_delete')) {
		echo "	<th class='checkbox'>\n";
		echo "		<input type='checkbox' id='checkbox_all' name='checkbox_all' onclick='list_all_toggle(); checkbox_on_change(this);' ".(is_array($rules) && count($rules) > 0 ? '' : "style='visibility: hidden;'").">\n";
		echo "	</th>\n";
	}
	echo "	<th>".($text['label-department'] ?? 'Department')."</th>\n";
	echo "	<th>".($text['label-keywords'] ?? 'Keywords')."</th>\n";
	echo "	<th>".($text['label-extension'] ?? 'Extension')."</th>\n";
	echo "	<th class='center'>".($text['label-message'] ?? 'Message')."</th>\n";
	echo "	<th>".($text['label-secretary'] ?? 'Secretary')."</th>\n";
	echo "	<th>".($text['label-priority'] ?? 'Priority')."</th>\n";
	echo "	<th class='center'>".($text['label-status'] ?? 'Status')."</th>\n";
	echo "</tr>\n";

	if (is_array($rules) && count($rules) > 0) {
		$x = 0;
		foreach ($rules as $row) {
			$list_row_onclick = "if (!shift_key_pressed) { window.location='transfer_rules_edit.php?id=".urlencode($row['transfer_rule_uuid'] ?? '')."'; }";
			echo "<tr class='list-row' href='transfer_rules_edit.php?id=".urlencode($row['transfer_rule_uuid'] ?? '')."' onclick=\"".$list_row_onclick."\">\n";
			if (permission_exists('voice_secretary_delete')) {
				echo "	<td class='checkbox'>\n";
				echo "		<input type='checkbox' name='rules[]' id='checkbox_".$x."' value='".escape($row['transfer_rule_uuid'] ?? '')."' onclick=\"checkbox_on_change(this); event.stopPropagation();\">\n";
				echo "		<label class='checkbox-label' for='checkbox_".$x."'></label>\n";
				echo "	</td>\n";
			}
			echo "	<td>".escape($row['department_name'] ?? '')."</td>\n";
			
			$keywords = json_decode($row['keywords'] ?? '[]', true) ?: [];
			$keywords_display = implode(', ', array_slice($keywords, 0, 5));
			if (count($keywords) > 5) $keywords_display .= '...';
			echo "	<td>".escape($keywords_display)."</td>\n";
			
			echo "	<td>".escape($row['transfer_extension'] ?? '')."</td>\n";
			
			// Mostrar ícone se tem mensagem configurada
			echo "	<td class='center'>\n";
			if (!empty($row['transfer_message'])) {
				echo "		<span title='".escape($row['transfer_message'])."' style='cursor:help;'>✓</span>\n";
			} else {
				echo "		<span style='color:#999;'>—</span>\n";
			}
			echo "	</td>\n";
			
			echo "	<td>".escape($row['secretary_name'] ?? '—')."</td>\n";
			echo "	<td>".intval($row['priority'] ?? 10)."</td>\n";
			
			echo "	<td class='center'>\n";
			if (!empty($row['is_active'])) {
				echo "		<span style='background:#28a745;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;'>".($text['label-active'] ?? 'Active')."</span>\n";
			} else {
				echo "		<span style='background:#6c757d;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;'>".($text['label-inactive'] ?? 'Inactive')."</span>\n";
			}
			echo "	</td>\n";
			echo "</tr>\n";
			$x++;
		}
	} else {
		echo "<tr><td colspan='8'>".($text['message-no_rules'] ?? 'No transfer rules found.')."</td></tr>\n";
	}

	echo "</table>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

//include the footer
	require_once "resources/footer.php";

?>
