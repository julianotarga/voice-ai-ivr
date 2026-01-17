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
		
		$transfer_extension = trim($_POST['transfer_extension'] ?? '');
		$transfer_message = trim($_POST['transfer_message'] ?? '');
		
		$form_data = [
			'department_name' => $_POST['department_name'] ?? '',
			'keywords' => json_encode($keywords),
			'transfer_extension' => $transfer_extension,
			'transfer_message' => !empty($transfer_message) ? $transfer_message : null,
			'voice_secretary_uuid' => !empty($_POST['voice_secretary_uuid']) ? $_POST['voice_secretary_uuid'] : null,
			'priority' => intval($_POST['priority'] ?? 10),
			'is_active' => isset($_POST['is_active']) ? 'true' : 'false',
		];
		
		// Validação de campos obrigatórios
		if (empty($form_data['department_name']) || empty($form_data['transfer_extension'])) {
			message::add($text['message-required'] ?? 'Required fields missing', 'negative');
		}
		// Validação de extensão válida (somente números, *, # e até 20 caracteres)
		elseif (!preg_match('/^[0-9*#]{1,20}$/', $transfer_extension)) {
			message::add($text['message-invalid_extension'] ?? 'Invalid extension. Use only digits, * or # (max 20 chars).', 'negative');
		} else {
			// Verificar se extensão existe no sistema (aviso, não bloqueia)
			$sql = "SELECT 1 FROM v_extensions WHERE domain_uuid = :domain_uuid AND extension = :ext AND enabled = 'true'
					UNION SELECT 1 FROM v_ring_groups WHERE domain_uuid = :domain_uuid AND ring_group_extension = :ext AND ring_group_enabled = 'true'
					UNION SELECT 1 FROM v_call_center_queues WHERE domain_uuid = :domain_uuid AND queue_extension = :ext AND queue_enabled = 'true'";
			$parameters['domain_uuid'] = $domain_uuid;
			$parameters['ext'] = $transfer_extension;
			$ext_exists = $database->select($sql, $parameters, 'all');
			unset($parameters);
			
			if (empty($ext_exists)) {
				// Aviso, não erro - permite salvar mesmo assim
				message::add($text['message-extension_warning'] ?? 'Warning: Extension not found in the system. It may be external or not configured yet.', 'alert');
			}
			if ($action === 'add') {
				$form_data['uuid'] = uuid();
				$form_data['domain_uuid'] = $domain_uuid;
				$sql = "INSERT INTO v_voice_transfer_rules (
					transfer_rule_uuid, domain_uuid, department_name, keywords,
					transfer_extension, transfer_message, voice_secretary_uuid, priority, is_active, insert_date
				) VALUES (
					:uuid, :domain_uuid, :department_name, :keywords,
					:transfer_extension, :transfer_message, :voice_secretary_uuid, :priority, :is_active, NOW()
				)";
			} else {
				$form_data['uuid'] = $rule_uuid;
				$form_data['domain_uuid'] = $domain_uuid;
				$sql = "UPDATE v_voice_transfer_rules SET 
					department_name = :department_name, keywords = :keywords,
					transfer_extension = :transfer_extension, transfer_message = :transfer_message,
					voice_secretary_uuid = :voice_secretary_uuid,
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

//get extensions, ring groups and call center queues for validation/autocomplete
	$valid_destinations = [];
	
	// Extensions
	$sql = "SELECT extension FROM v_extensions WHERE domain_uuid = :domain_uuid AND enabled = 'true' ORDER BY extension";
	$parameters['domain_uuid'] = $domain_uuid;
	$extensions = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);
	foreach ($extensions as $ext) {
		$valid_destinations[] = $ext['extension'];
	}
	
	// Ring Groups
	$sql = "SELECT ring_group_extension FROM v_ring_groups WHERE domain_uuid = :domain_uuid AND ring_group_enabled = 'true' ORDER BY ring_group_extension";
	$parameters['domain_uuid'] = $domain_uuid;
	$ring_groups = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);
	foreach ($ring_groups as $rg) {
		$valid_destinations[] = $rg['ring_group_extension'];
	}
	
	// Call Center Queues
	$sql = "SELECT queue_extension FROM v_call_center_queues WHERE domain_uuid = :domain_uuid AND queue_enabled = 'true' ORDER BY queue_extension";
	$parameters['domain_uuid'] = $domain_uuid;
	$queues = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);
	foreach ($queues as $q) {
		$valid_destinations[] = $q['queue_extension'];
	}

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

	echo "<div style='background: #e7f3ff; border-left: 4px solid #2196F3; padding: 12px 15px; margin-bottom: 15px; border-radius: 4px;'>\n";
	echo "	<b style='color: #1976D2;'>💡 Como Funciona:</b><br/>\n";
	echo "	<span style='color: #555;'>As Regras de Transferência permitem direcionar chamadas para departamentos específicos. "
		. "Quando o cliente menciona uma <b>keyword</b> (ex: \"quero falar com vendas\"), a IA transfere automaticamente para o ramal configurado.<br/><br/>"
		. "⚠️ <b>Importante:</b> Não confunda com 'Handoff Keywords' da Secretary - aquelas são para handoff genérico (\"quero falar com atendente\").</span>\n";
	echo "</div>\n";

	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-department'] ?? 'Department')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='department_name' maxlength='255' value='".escape($data['department_name'] ?? '')."' required placeholder='Ex: Vendas, Financeiro, Suporte'>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "Nome do departamento que será identificado pela IA. "
		. "A IA usará este nome para confirmar a transferência: <i>\"Transferindo para o setor de <b>[Departamento]</b>...\"</i>"
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-keywords'] ?? 'Keywords')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$keywords = isset($data['keywords']) ? json_decode($data['keywords'], true) : [];
	echo "		<textarea class='formfld' name='keywords' rows='3' style='width: 100%;' required placeholder='vendas, comprar, preço, orçamento, produto'>".escape(implode(', ', $keywords ?: []))."</textarea>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>Palavras-chave de INTENÇÃO</b> que identificam este departamento. Separe por vírgula.<br/>"
		. "Exemplo para Vendas: <code>vendas, comprar, preço, orçamento, catálogo, produto</code><br/>"
		. "⚠️ <b>NÃO use termos genéricos</b> como 'atendente', 'humano' - esses devem ficar em Handoff Keywords na Secretary."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-extension'] ?? 'Extension')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='transfer_extension' maxlength='20' value='".escape($data['transfer_extension'] ?? '')."' required pattern='[0-9*#]{1,20}' title='".($text['description-extension_pattern'] ?? 'Only digits, * or # (max 20 chars)')."' list='extensions_list' autocomplete='off' placeholder='Ex: 1001'>\n";
	echo "		<datalist id='extensions_list'>\n";
	foreach ($valid_destinations as $dest) {
		echo "			<option value='".escape($dest)."'>\n";
	}
	echo "		</datalist>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "<b>Ramal ESPECÍFICO do departamento.</b> Pode ser: Ramal direto, Ring Group ou Fila de Call Center.<br/>"
		. "⚠️ <b>NÃO use o mesmo ramal</b> do Transfer Extension da Secretary (aquele é para handoff genérico)."
		. "</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-transfer_message'] ?? 'Transfer Message')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<textarea class='formfld' name='transfer_message' rows='2' style='width: 100%;' maxlength='500' placeholder='Ex: Vou transferir você para o nosso setor de vendas. Um momento, por favor.'>".escape($data['transfer_message'] ?? '')."</textarea>\n";
	echo "		<br /><span class='vtable-hint' style='color: #555;'>"
		. "Mensagem opcional que a IA falará <b>antes</b> de iniciar a transferência. "
		. "Se deixar em branco, a IA usará uma mensagem padrão."
		. "</span>\n";
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
