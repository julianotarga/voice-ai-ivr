<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Transfer Rule Edit Page
	Create or edit transfer rules.
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

//get domain_uuid from session
	$domain_uuid = $_SESSION['domain_uuid'] ?? null;
	if (!$domain_uuid) {
		echo "access denied";
		exit;
	}

//initialize
	$database = new database;
	$action = 'add';
	$data = [];

//check if editing existing
	if (isset($_GET['id']) && is_uuid($_GET['id'])) {
		$action = 'edit';
		$rule_uuid = $_GET['id'];
		
		$sql = "SELECT * FROM v_voice_transfer_rules WHERE transfer_rule_uuid = :uuid AND domain_uuid = :domain_uuid";
		$parameters['uuid'] = $rule_uuid;
		$parameters['domain_uuid'] = $domain_uuid;
		$rows = $database->select($sql, $parameters, 'all');
		unset($parameters);
		
		if (!$rows) {
			message::add($text['message-invalid_id'] ?? 'Invalid ID', 'negative');
			header('Location: transfer_rules.php');
			exit;
		}
		$data = $rows[0];
	}

//process form submission
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && count($_POST) > 0) {
		//validate token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: transfer_rules.php');
			exit;
		}

		$keywords = array_filter(array_map('trim', explode(',', $_POST['keywords'] ?? '')));
		
		$form_data = [
			'department_name' => $_POST['department_name'] ?? '',
			'keywords' => json_encode($keywords),
			'transfer_extension' => $_POST['transfer_extension'] ?? '',
			'voice_secretary_uuid' => !empty($_POST['voice_secretary_uuid']) ? $_POST['voice_secretary_uuid'] : null,
			'priority' => intval($_POST['priority'] ?? 10),
			'is_active' => isset($_POST['is_active']) ? 'true' : 'false',
		];
		
		if (empty($form_data['department_name']) || empty($form_data['transfer_extension'])) {
			message::add($text['message-required'] ?? 'Required fields missing', 'negative');
		} else {
			if ($action === 'add') {
				$form_data['uuid'] = uuid();
				$form_data['domain_uuid'] = $domain_uuid;
				$sql = "INSERT INTO v_voice_transfer_rules (
					transfer_rule_uuid, domain_uuid, department_name, keywords,
					transfer_extension, voice_secretary_uuid, priority, is_active, insert_date
				) VALUES (
					:uuid, :domain_uuid, :department_name, :keywords,
					:transfer_extension, :voice_secretary_uuid, :priority, :is_active, NOW()
				)";
			} else {
				$form_data['uuid'] = $rule_uuid;
				$form_data['domain_uuid'] = $domain_uuid;
				$sql = "UPDATE v_voice_transfer_rules SET 
					department_name = :department_name, keywords = :keywords,
					transfer_extension = :transfer_extension, voice_secretary_uuid = :voice_secretary_uuid,
					priority = :priority, is_active = :is_active, update_date = NOW()
					WHERE transfer_rule_uuid = :uuid AND domain_uuid = :domain_uuid";
			}
			
			$database->execute($sql, $form_data);
			
			if ($action === 'add') {
				message::add($text['message-add']);
			} else {
				message::add($text['message-update']);
			}
			header('Location: transfer_rules.php');
			exit;
		}
	}

//get secretaries for dropdown
	$sql = "SELECT voice_secretary_uuid, secretary_name FROM v_voice_secretaries WHERE domain_uuid = :domain_uuid ORDER BY secretary_name";
	$parameters['domain_uuid'] = $domain_uuid;
	$secretaries = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = ($action === 'add') 
		? ($text['title-add_rule'] ?? 'Add Transfer Rule') 
		: ($text['title-edit_rule'] ?? 'Edit Transfer Rule');
	require_once "resources/header.php";

//show the content
	echo "<form method='post' name='frm' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'button','label'=>$text['button-back'],'icon'=>$_SESSION['theme']['button_icon_back'],'id'=>'btn_back','link'=>'transfer_rules.php']);
	echo button::create(['type'=>'submit','label'=>$text['button-save'],'icon'=>$_SESSION['theme']['button_icon_save'],'id'=>'btn_save','style'=>'margin-left: 15px;']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-transfer_rule'] ?? 'Configure a transfer rule based on keywords.')."\n";
	echo "<br /><br />\n";

	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-department'] ?? 'Department')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='department_name' maxlength='255' value='".escape($data['department_name'] ?? '')."' required>\n";
	echo "		<br />".($text['description-department'] ?? 'Name of the department or sector.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-keywords'] ?? 'Keywords')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$keywords = isset($data['keywords']) ? json_decode($data['keywords'], true) : [];
	echo "		<textarea class='formfld' name='keywords' rows='3' style='width: 100%;' required>".escape(implode(', ', $keywords ?: []))."</textarea>\n";
	echo "		<br />".($text['description-keywords'] ?? 'Comma-separated keywords that trigger this transfer.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-extension'] ?? 'Extension')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='transfer_extension' maxlength='20' value='".escape($data['transfer_extension'] ?? '')."' required>\n";
	echo "		<br />".($text['description-extension'] ?? 'Extension to transfer the call to.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-secretary'] ?? 'Secretary')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='voice_secretary_uuid'>\n";
	echo "			<option value=''>".($text['option-all'] ?? 'All')."</option>\n";
	foreach ($secretaries as $s) {
		$selected = (($data['voice_secretary_uuid'] ?? '') === $s['voice_secretary_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($s['voice_secretary_uuid'])."' ".$selected.">".escape($s['secretary_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "		<br />".($text['description-secretary'] ?? 'Apply rule only to this secretary, or leave blank for all.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-priority'] ?? 'Priority')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='priority' min='1' max='100' value='".intval($data['priority'] ?? 10)."'>\n";
	echo "		<br />".($text['description-priority'] ?? 'Lower number = higher priority.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-status'] ?? 'Status')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$is_active = (!isset($data['is_active']) || $data['is_active'] == 'true' || $data['is_active'] === true);
	echo "		<input type='checkbox' name='is_active' id='is_active' ".($is_active ? 'checked' : '').">\n";
	echo "		<label for='is_active'>".($text['label-active'] ?? 'Active')."</label>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

//include the footer
	require_once "resources/footer.php";

?>
