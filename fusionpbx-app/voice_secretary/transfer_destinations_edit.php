<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Transfer Destination Edit
	Criar ou editar destino de transferência (handoff).
	
	Tipos de destino suportados:
	- extension: Ramal individual
	- ring_group: Grupo de toque
	- queue: Fila de call center
	- external: Número externo
	- voicemail: Caixa postal
	
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
		$destination_uuid = $_GET['id'];
		
		$sql = "SELECT * FROM v_voice_transfer_destinations WHERE transfer_destination_uuid = :uuid AND domain_uuid = :domain_uuid";
		$parameters['uuid'] = $destination_uuid;
		$parameters['domain_uuid'] = $domain_uuid;
		$rows = $database->select($sql, $parameters, 'all');
		unset($parameters);
		
		if (!$rows) {
			message::add($text['message-invalid_id'] ?? 'Invalid ID', 'negative');
			header('Location: transfer_destinations.php');
			exit;
		}
		$data = $rows[0];
	}

//destination types
	$destination_types = [
		'extension' => ['label' => 'Ramal Individual', 'icon' => '📞', 'hint' => 'Transfere para um ramal específico'],
		'ring_group' => ['label' => 'Ring Group', 'icon' => '👥', 'hint' => 'Toca em vários ramais simultaneamente'],
		'queue' => ['label' => 'Fila de CallCenter', 'icon' => '📋', 'hint' => 'Entra na fila e aguarda atendente'],
		'external' => ['label' => 'Número Externo', 'icon' => '🌐', 'hint' => 'Transfere para número externo (celular/fixo)'],
		'voicemail' => ['label' => 'Voicemail', 'icon' => '📧', 'hint' => 'Envia para caixa postal']
	];

//fallback actions
	$fallback_actions = [
		'offer_ticket' => ['label' => 'Oferecer Ticket', 'hint' => 'Pergunta se quer deixar recado para criar ticket'],
		'create_ticket' => ['label' => 'Criar Ticket Automático', 'hint' => 'Cria ticket automaticamente com resumo da conversa'],
		'voicemail' => ['label' => 'Voicemail', 'hint' => 'Transfere para caixa postal do destino'],
		'return_agent' => ['label' => 'Voltar ao Agente IA', 'hint' => 'Retorna para a secretária virtual continuar atendimento'],
		'hangup' => ['label' => 'Desligar', 'hint' => 'Encerra a chamada com mensagem de despedida']
	];

//process form submission
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && count($_POST) > 0) {
		//validate token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: transfer_destinations.php');
			exit;
		}

		// Parse aliases
		$aliases_raw = array_filter(array_map('trim', explode(',', $_POST['aliases'] ?? '')));
		$aliases = array_values($aliases_raw);
		
		// Parse working hours
		$working_hours = null;
		if (!empty($_POST['working_hours_enabled'])) {
			$working_hours = [
				'start' => $_POST['working_hours_start'] ?? '08:00',
				'end' => $_POST['working_hours_end'] ?? '18:00',
				'days' => array_map('intval', $_POST['working_hours_days'] ?? [1,2,3,4,5]),
				'timezone' => $_POST['working_hours_timezone'] ?? 'America/Sao_Paulo'
			];
		}
		
		$form_data = [
			'name' => trim($_POST['name'] ?? ''),
			'aliases' => json_encode($aliases),
			'destination_type' => $_POST['destination_type'] ?? 'extension',
			'destination_number' => trim($_POST['destination_number'] ?? ''),
			'destination_context' => trim($_POST['destination_context'] ?? 'default'),
			'ring_timeout_seconds' => intval($_POST['ring_timeout_seconds'] ?? 30),
			'max_retries' => intval($_POST['max_retries'] ?? 1),
			'retry_delay_seconds' => intval($_POST['retry_delay_seconds'] ?? 5),
			'fallback_action' => $_POST['fallback_action'] ?? 'offer_ticket',
			'department' => trim($_POST['department'] ?? '') ?: null,
			'role' => trim($_POST['role'] ?? '') ?: null,
			'description' => trim($_POST['description'] ?? '') ?: null,
			'working_hours' => $working_hours ? json_encode($working_hours) : null,
			'priority' => intval($_POST['priority'] ?? 100),
			'is_enabled' => isset($_POST['is_enabled']) ? true : false,
			'is_default' => isset($_POST['is_default']) ? true : false,
			'secretary_uuid' => !empty($_POST['secretary_uuid']) ? $_POST['secretary_uuid'] : null,
		];
		
		// Validação
		$errors = [];
		if (empty($form_data['name'])) {
			$errors[] = 'Nome é obrigatório';
		}
		if (empty($form_data['destination_number'])) {
			$errors[] = 'Número/Ramal de destino é obrigatório';
		}
		if (!array_key_exists($form_data['destination_type'], $destination_types)) {
			$errors[] = 'Tipo de destino inválido';
		}
		if (!array_key_exists($form_data['fallback_action'], $fallback_actions)) {
			$errors[] = 'Ação de fallback inválida';
		}
		
		if (!empty($errors)) {
			foreach ($errors as $error) {
				message::add($error, 'negative');
			}
		} else {
			// Verificar se está marcando como default
			if ($form_data['is_default']) {
				// Remover default de outros
				$sql = "UPDATE v_voice_transfer_destinations SET is_default = false WHERE domain_uuid = :domain_uuid AND is_default = true";
				$parameters['domain_uuid'] = $domain_uuid;
				$database->execute($sql, $parameters);
				unset($parameters);
			}
			
			if ($action === 'add') {
				$form_data['uuid'] = uuid();
				$form_data['domain_uuid'] = $domain_uuid;
				$sql = "INSERT INTO v_voice_transfer_destinations (
					transfer_destination_uuid, domain_uuid, secretary_uuid,
					name, aliases, destination_type, destination_number, destination_context,
					ring_timeout_seconds, max_retries, retry_delay_seconds,
					fallback_action, department, role, description, working_hours,
					priority, is_enabled, is_default, created_at
				) VALUES (
					:uuid, :domain_uuid, :secretary_uuid,
					:name, :aliases, :destination_type, :destination_number, :destination_context,
					:ring_timeout_seconds, :max_retries, :retry_delay_seconds,
					:fallback_action, :department, :role, :description, :working_hours,
					:priority, :is_enabled, :is_default, NOW()
				)";
			} else {
				$form_data['uuid'] = $destination_uuid;
				$form_data['domain_uuid'] = $domain_uuid;
				$sql = "UPDATE v_voice_transfer_destinations SET 
					secretary_uuid = :secretary_uuid,
					name = :name, aliases = :aliases,
					destination_type = :destination_type, destination_number = :destination_number,
					destination_context = :destination_context,
					ring_timeout_seconds = :ring_timeout_seconds, max_retries = :max_retries,
					retry_delay_seconds = :retry_delay_seconds, fallback_action = :fallback_action,
					department = :department, role = :role, description = :description,
					working_hours = :working_hours,
					priority = :priority, is_enabled = :is_enabled, is_default = :is_default,
					updated_at = NOW()
					WHERE transfer_destination_uuid = :uuid AND domain_uuid = :domain_uuid";
			}
			
			$database->execute($sql, $form_data);
			
			if ($action === 'add') {
				message::add($text['message-add']);
			} else {
				message::add($text['message-update']);
			}
			header('Location: transfer_destinations.php');
			exit;
		}
	}

//get secretaries for dropdown
	$sql = "SELECT voice_secretary_uuid, secretary_name FROM v_voice_secretaries WHERE domain_uuid = :domain_uuid ORDER BY secretary_name";
	$parameters['domain_uuid'] = $domain_uuid;
	$secretaries = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//get extensions for datalist
	$sql = "SELECT extension, effective_caller_id_name FROM v_extensions WHERE domain_uuid = :domain_uuid AND enabled = 'true' ORDER BY extension";
	$parameters['domain_uuid'] = $domain_uuid;
	$extensions = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//get ring groups for datalist
	$sql = "SELECT ring_group_extension, ring_group_name FROM v_ring_groups WHERE domain_uuid = :domain_uuid AND ring_group_enabled = 'true' ORDER BY ring_group_extension";
	$parameters['domain_uuid'] = $domain_uuid;
	$ring_groups = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//get call center queues for datalist
	$sql = "SELECT queue_extension, queue_name FROM v_call_center_queues WHERE domain_uuid = :domain_uuid AND queue_enabled = 'true' ORDER BY queue_extension";
	$parameters['domain_uuid'] = $domain_uuid;
	$queues = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//parse existing working hours
	$working_hours = null;
	if (!empty($data['working_hours'])) {
		$working_hours = json_decode($data['working_hours'], true);
	}

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = ($action === 'add') 
		? ($text['title-add_destination'] ?? 'Adicionar Destino de Transferência') 
		: ($text['title-edit_destination'] ?? 'Editar Destino de Transferência');
	require_once "resources/header.php";

//show the content
	echo "<form method='post' name='frm' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'button','label'=>$text['button-back'],'icon'=>$_SESSION['theme']['button_icon_back'],'id'=>'btn_back','link'=>'transfer_destinations.php']);
	echo button::create(['type'=>'submit','label'=>$text['button-save'],'icon'=>$_SESSION['theme']['button_icon_save'],'id'=>'btn_save','style'=>'margin-left: 15px;']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo "<p>".($text['description-transfer_destination'] ?? 'Configure um destino de transferência para handoff inteligente.')."</p>\n";

	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	// ========================================
	// Identificação
	// ========================================
	echo "<tr><td colspan='2'><h3 style='margin:20px 0 10px 0;border-bottom:1px solid #ddd;padding-bottom:5px;'>📋 Identificação</h3></td></tr>\n";

	// Nome
	echo "<tr>\n";
	echo "	<td width='30%' class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-name'] ?? 'Nome')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='name' maxlength='100' value='".escape($data['name'] ?? '')."' required style='width:300px;'>\n";
	echo "		<br /><span class='description'>".($text['description-name'] ?? 'Nome usado para identificação por voz (ex: "Financeiro", "João do Suporte").')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Departamento
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-department'] ?? 'Departamento')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='department' maxlength='100' value='".escape($data['department'] ?? '')."' style='width:200px;' list='dept_list'>\n";
	echo "		<datalist id='dept_list'>\n";
	echo "			<option value='Financeiro'>\n";
	echo "			<option value='Comercial'>\n";
	echo "			<option value='Suporte'>\n";
	echo "			<option value='SAC'>\n";
	echo "			<option value='Vendas'>\n";
	echo "			<option value='Atendimento'>\n";
	echo "			<option value='TI'>\n";
	echo "			<option value='RH'>\n";
	echo "		</datalist>\n";
	echo "		<br /><span class='description'>".($text['description-department'] ?? 'Departamento para categorização.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Função/Cargo
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-role'] ?? 'Função/Cargo')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='role' maxlength='100' value='".escape($data['role'] ?? '')."' style='width:200px;'>\n";
	echo "		<br /><span class='description'>".($text['description-role'] ?? 'Função da pessoa ou setor (ex: "Gerente", "Atendente").')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Aliases (keywords)
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-aliases'] ?? 'Aliases/Palavras-chave')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$aliases = isset($data['aliases']) ? json_decode($data['aliases'], true) : [];
	echo "		<textarea class='formfld' name='aliases' rows='2' style='width: 100%;'>".escape(implode(', ', $aliases ?: []))."</textarea>\n";
	echo "		<br /><span class='description'>".($text['description-aliases'] ?? 'Palavras-chave separadas por vírgula para identificação (ex: "boleto, pagamento, segunda via, cobrança").')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Descrição
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-description'] ?? 'Descrição')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<textarea class='formfld' name='description' rows='2' style='width: 100%;' maxlength='500'>".escape($data['description'] ?? '')."</textarea>\n";
	echo "		<br /><span class='description'>".($text['description-description'] ?? 'Descrição interna para referência.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// ========================================
	// Destino
	// ========================================
	echo "<tr><td colspan='2'><h3 style='margin:30px 0 10px 0;border-bottom:1px solid #ddd;padding-bottom:5px;'>📞 Destino da Transferência</h3></td></tr>\n";

	// Tipo de destino
	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-destination_type'] ?? 'Tipo de Destino')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='destination_type' id='destination_type' onchange='updateDestinationHints()' style='width:250px;'>\n";
	foreach ($destination_types as $type_key => $type_info) {
		$selected = (($data['destination_type'] ?? 'extension') === $type_key) ? 'selected' : '';
		echo "			<option value='".escape($type_key)."' ".$selected.">".$type_info['icon']." ".$type_info['label']."</option>\n";
	}
	echo "		</select>\n";
	echo "		<br /><span class='description' id='destination_type_hint'>".($destination_types[$data['destination_type'] ?? 'extension']['hint'] ?? '')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Número/Ramal de destino
	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-destination_number'] ?? 'Ramal/Número')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='destination_number' maxlength='50' value='".escape($data['destination_number'] ?? '')."' required style='width:150px;' list='all_destinations'>\n";
	echo "		<datalist id='all_destinations'>\n";
	foreach ($extensions as $ext) {
		echo "			<option value='".escape($ext['extension'])."'>📞 ".escape($ext['extension'])." - ".escape($ext['effective_caller_id_name'] ?? '')."</option>\n";
	}
	foreach ($ring_groups as $rg) {
		echo "			<option value='".escape($rg['ring_group_extension'])."'>👥 ".escape($rg['ring_group_extension'])." - ".escape($rg['ring_group_name'] ?? '')."</option>\n";
	}
	foreach ($queues as $q) {
		echo "			<option value='".escape($q['queue_extension'])."'>📋 ".escape($q['queue_extension'])." - ".escape($q['queue_name'] ?? '')."</option>\n";
	}
	echo "		</datalist>\n";
	echo "		<br /><span class='description'>".($text['description-destination_number'] ?? 'Ramal, extensão do ring group, fila ou número externo.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Contexto (avançado)
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-context'] ?? 'Contexto')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='destination_context' maxlength='50' value='".escape($data['destination_context'] ?? 'default')."' style='width:150px;'>\n";
	echo "		<br /><span class='description'>".($text['description-context'] ?? 'Contexto do dialplan (normalmente "default"). Altere apenas se souber o que está fazendo.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// ========================================
	// Configurações de Chamada
	// ========================================
	echo "<tr><td colspan='2'><h3 style='margin:30px 0 10px 0;border-bottom:1px solid #ddd;padding-bottom:5px;'>⏱️ Configurações de Chamada</h3></td></tr>\n";

	// Timeout
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-ring_timeout'] ?? 'Timeout de Toque')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='ring_timeout_seconds' min='5' max='120' value='".intval($data['ring_timeout_seconds'] ?? 30)."' style='width:80px;'> segundos\n";
	echo "		<br /><span class='description'>".($text['description-ring_timeout'] ?? 'Tempo máximo de espera antes de executar fallback (5-120s).')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Retentativas
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-max_retries'] ?? 'Retentativas')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='max_retries' min='0' max='5' value='".intval($data['max_retries'] ?? 1)."' style='width:60px;'>\n";
	echo "		<br /><span class='description'>".($text['description-max_retries'] ?? 'Número de tentativas adicionais se não atender (0-5).')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Delay entre retentativas
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-retry_delay'] ?? 'Delay entre Tentativas')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='retry_delay_seconds' min='1' max='30' value='".intval($data['retry_delay_seconds'] ?? 5)."' style='width:60px;'> segundos\n";
	echo "		<br /><span class='description'>".($text['description-retry_delay'] ?? 'Tempo de espera entre tentativas (1-30s).')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Fallback action
	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-fallback_action'] ?? 'Se Não Atender')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='fallback_action' id='fallback_action' onchange='updateFallbackHint()' style='width:250px;'>\n";
	foreach ($fallback_actions as $action_key => $action_info) {
		$selected = (($data['fallback_action'] ?? 'offer_ticket') === $action_key) ? 'selected' : '';
		echo "			<option value='".escape($action_key)."' ".$selected.">".$action_info['label']."</option>\n";
	}
	echo "		</select>\n";
	echo "		<br /><span class='description' id='fallback_action_hint'>".($fallback_actions[$data['fallback_action'] ?? 'offer_ticket']['hint'] ?? '')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// ========================================
	// Horário de Funcionamento (opcional)
	// ========================================
	echo "<tr><td colspan='2'><h3 style='margin:30px 0 10px 0;border-bottom:1px solid #ddd;padding-bottom:5px;'>🕐 Horário de Funcionamento (Opcional)</h3></td></tr>\n";

	// Habilitar horário
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-working_hours_enabled'] ?? 'Restringir Horário')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$wh_enabled = !empty($working_hours);
	echo "		<input type='checkbox' name='working_hours_enabled' id='working_hours_enabled' ".($wh_enabled ? 'checked' : '')." onchange='toggleWorkingHours()'>\n";
	echo "		<label for='working_hours_enabled'>".($text['label-enable_working_hours'] ?? 'Habilitar restrição de horário')."</label>\n";
	echo "		<br /><span class='description'>".($text['description-working_hours'] ?? 'Se habilitado, transferência só funciona no horário configurado.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Container de horário
	echo "<tr id='working_hours_container' style='".($wh_enabled ? '' : 'display:none;')."'>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-schedule'] ?? 'Horário')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input type='time' name='working_hours_start' value='".escape($working_hours['start'] ?? '08:00')."' style='width:100px;'>\n";
	echo "		até\n";
	echo "		<input type='time' name='working_hours_end' value='".escape($working_hours['end'] ?? '18:00')."' style='width:100px;'>\n";
	echo "		<br /><br />\n";
	
	$days_enabled = $working_hours['days'] ?? [1,2,3,4,5];
	$day_names = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
	for ($d = 0; $d < 7; $d++) {
		$checked = in_array($d, $days_enabled) ? 'checked' : '';
		echo "		<label style='margin-right:15px;'><input type='checkbox' name='working_hours_days[]' value='".$d."' ".$checked."> ".$day_names[$d]."</label>\n";
	}
	echo "		<br /><br />\n";
	echo "		<label>Timezone: <input type='text' name='working_hours_timezone' value='".escape($working_hours['timezone'] ?? 'America/Sao_Paulo')."' style='width:200px;'></label>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// ========================================
	// Vinculação e Controle
	// ========================================
	echo "<tr><td colspan='2'><h3 style='margin:30px 0 10px 0;border-bottom:1px solid #ddd;padding-bottom:5px;'>⚙️ Configurações Gerais</h3></td></tr>\n";

	// Secretária (opcional)
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-secretary'] ?? 'Secretária')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<select class='formfld' name='secretary_uuid' style='width:250px;'>\n";
	echo "			<option value=''>".($text['option-all'] ?? 'Todas as Secretárias')."</option>\n";
	foreach ($secretaries as $s) {
		$selected = (($data['secretary_uuid'] ?? '') === $s['voice_secretary_uuid']) ? 'selected' : '';
		echo "			<option value='".escape($s['voice_secretary_uuid'])."' ".$selected.">".escape($s['secretary_name'])."</option>\n";
	}
	echo "		</select>\n";
	echo "		<br /><span class='description'>".($text['description-secretary_dest'] ?? 'Se especificada, destino só aparece para esta secretária.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Prioridade
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-priority'] ?? 'Prioridade')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='number' name='priority' min='1' max='999' value='".intval($data['priority'] ?? 100)."' style='width:80px;'>\n";
	echo "		<br /><span class='description'>".($text['description-priority'] ?? 'Menor número = maior prioridade na ordenação.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Default
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-default'] ?? 'Destino Padrão')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$is_default = isset($data['is_default']) && ($data['is_default'] === true || $data['is_default'] === 't' || $data['is_default'] === 'true');
	echo "		<input type='checkbox' name='is_default' id='is_default' ".($is_default ? 'checked' : '').">\n";
	echo "		<label for='is_default'>".($text['label-set_as_default'] ?? 'Definir como destino padrão')."</label>\n";
	echo "		<br /><span class='description'>".($text['description-default'] ?? 'Usado quando cliente pede atendente humano sem especificar setor.')."</span>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	// Status
	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-status'] ?? 'Status')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$is_enabled = (!isset($data['is_enabled']) || $data['is_enabled'] === true || $data['is_enabled'] === 't' || $data['is_enabled'] === 'true');
	echo "		<input type='checkbox' name='is_enabled' id='is_enabled' ".($is_enabled ? 'checked' : '').">\n";
	echo "		<label for='is_enabled'>".($text['label-enabled'] ?? 'Ativo')."</label>\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

// JavaScript para hints dinâmicos
?>
<script>
const destinationTypes = <?php echo json_encode($destination_types); ?>;
const fallbackActions = <?php echo json_encode($fallback_actions); ?>;

function updateDestinationHints() {
	const type = document.getElementById('destination_type').value;
	const hint = destinationTypes[type]?.hint || '';
	document.getElementById('destination_type_hint').textContent = hint;
}

function updateFallbackHint() {
	const action = document.getElementById('fallback_action').value;
	const hint = fallbackActions[action]?.hint || '';
	document.getElementById('fallback_action_hint').textContent = hint;
}

function toggleWorkingHours() {
	const enabled = document.getElementById('working_hours_enabled').checked;
	document.getElementById('working_hours_container').style.display = enabled ? '' : 'none';
}
</script>
<?php

//include the footer
	require_once "resources/footer.php";

?>
