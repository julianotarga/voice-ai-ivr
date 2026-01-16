<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Transfer Destinations List
	Gerenciamento de destinos de transferência (handoff) para Voice AI.
	
	Funcionalidades:
	- Ramais individuais
	- Ring Groups (grupos de toque)
	- Filas de Call Center
	- Números externos
	- Voicemail
	
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
		$destinations = $_POST['destinations'] ?? [];
		
		if ($action === 'delete' && is_array($destinations) && count($destinations) > 0) {
			//validate token
			$token = new token;
			if (!$token->validate($_SERVER['PHP_SELF'])) {
				message::add($text['message-invalid_token'],'negative');
				header('Location: transfer_destinations.php');
				exit;
			}
			
			$database = new database;
			foreach ($destinations as $uuid) {
				if (is_uuid($uuid)) {
					$sql = "DELETE FROM v_voice_transfer_destinations WHERE transfer_destination_uuid = :uuid AND domain_uuid = :domain_uuid";
					$parameters['uuid'] = $uuid;
					$parameters['domain_uuid'] = $domain_uuid;
					$database->execute($sql, $parameters);
					unset($parameters);
				}
			}
			message::add($text['message-delete']);
			header('Location: transfer_destinations.php');
			exit;
		}
	}

//get transfer destinations
	$database = new database;
	$sql = "SELECT 
				d.transfer_destination_uuid, 
				d.name,
				d.department,
				d.destination_type,
				d.destination_number,
				d.ring_timeout_seconds,
				d.fallback_action,
				d.priority,
				d.is_enabled,
				d.is_default,
				d.working_hours,
				d.aliases,
				s.secretary_name
			FROM v_voice_transfer_destinations d
			LEFT JOIN v_voice_secretaries s ON s.voice_secretary_uuid = d.secretary_uuid
			WHERE d.domain_uuid = :domain_uuid 
			ORDER BY d.priority ASC, d.name ASC";
	$parameters['domain_uuid'] = $domain_uuid;
	$destinations = $database->select($sql, $parameters, 'all') ?: [];
	unset($parameters);

//destination type labels
	$destination_types = [
		'extension' => 'Ramal',
		'ring_group' => 'Ring Group',
		'queue' => 'Fila CallCenter',
		'external' => 'Externo',
		'voicemail' => 'Voicemail'
	];

//fallback action labels
	$fallback_actions = [
		'offer_ticket' => 'Oferecer Ticket',
		'create_ticket' => 'Criar Ticket Auto',
		'voicemail' => 'Voicemail',
		'return_agent' => 'Voltar ao Agente',
		'hangup' => 'Desligar'
	];

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = $text['title-transfer_destinations'] ?? 'Destinos de Transferência';
	require_once "resources/header.php";

//show the content
	echo "<form id='form_list' method='post'>\n";
	echo "<input type='hidden' name='action' id='action' value=''>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	if (permission_exists('voice_secretary_add')) {
		echo button::create(['type'=>'button','label'=>$text['button-add'],'icon'=>$_SESSION['theme']['button_icon_add'],'id'=>'btn_add','link'=>'transfer_destinations_edit.php']);
	}
	if (permission_exists('voice_secretary_delete') && is_array($destinations) && count($destinations) > 0) {
		echo button::create(['type'=>'button','label'=>$text['button-delete'],'icon'=>$_SESSION['theme']['button_icon_delete'],'id'=>'btn_delete','name'=>'btn_delete','style'=>'margin-left: 15px;','onclick'=>"if (confirm('".$text['confirm-delete']."')) { document.getElementById('action').value = 'delete'; document.getElementById('form_list').submit(); }"]);
	}
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo "<p>".($text['description-transfer_destinations'] ?? 'Configure destinos de transferência (handoff) para quando o cliente solicitar falar com um atendente humano. Suporta ramais, ring groups, filas de call center e números externos.')."</p>\n";

	echo "<table class='list'>\n";
	echo "<tr class='list-header'>\n";
	if (permission_exists('voice_secretary_delete')) {
		echo "	<th class='checkbox'>\n";
		echo "		<input type='checkbox' id='checkbox_all' name='checkbox_all' onclick='list_all_toggle(); checkbox_on_change(this);' ".(is_array($destinations) && count($destinations) > 0 ? '' : "style='visibility: hidden;'").">\n";
		echo "	</th>\n";
	}
	echo "	<th>".($text['label-name'] ?? 'Nome')."</th>\n";
	echo "	<th>".($text['label-department'] ?? 'Departamento')."</th>\n";
	echo "	<th>".($text['label-type'] ?? 'Tipo')."</th>\n";
	echo "	<th>".($text['label-destination'] ?? 'Destino')."</th>\n";
	echo "	<th class='center'>".($text['label-timeout'] ?? 'Timeout')."</th>\n";
	echo "	<th>".($text['label-fallback'] ?? 'Fallback')."</th>\n";
	echo "	<th>".($text['label-aliases'] ?? 'Aliases')."</th>\n";
	echo "	<th class='center'>".($text['label-priority'] ?? 'Prioridade')."</th>\n";
	echo "	<th class='center'>".($text['label-status'] ?? 'Status')."</th>\n";
	echo "</tr>\n";

	if (is_array($destinations) && count($destinations) > 0) {
		$x = 0;
		foreach ($destinations as $row) {
			$list_row_onclick = "if (!shift_key_pressed) { window.location='transfer_destinations_edit.php?id=".urlencode($row['transfer_destination_uuid'] ?? '')."'; }";
			echo "<tr class='list-row' href='transfer_destinations_edit.php?id=".urlencode($row['transfer_destination_uuid'] ?? '')."' onclick=\"".$list_row_onclick."\">\n";
			if (permission_exists('voice_secretary_delete')) {
				echo "	<td class='checkbox'>\n";
				echo "		<input type='checkbox' name='destinations[]' id='checkbox_".$x."' value='".escape($row['transfer_destination_uuid'] ?? '')."' onclick=\"checkbox_on_change(this); event.stopPropagation();\">\n";
				echo "		<label class='checkbox-label' for='checkbox_".$x."'></label>\n";
				echo "	</td>\n";
			}
			
			// Nome com indicador de default
			$name_display = escape($row['name'] ?? '');
			if ($row['is_default']) {
				$name_display .= ' <span style="background:#007bff;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;margin-left:5px;">DEFAULT</span>';
			}
			echo "	<td>".$name_display."</td>\n";
			
			echo "	<td>".escape($row['department'] ?? '—')."</td>\n";
			
			// Tipo com ícone
			$type = $row['destination_type'] ?? 'extension';
			$type_label = $destination_types[$type] ?? $type;
			$type_icons = [
				'extension' => '📞',
				'ring_group' => '👥',
				'queue' => '📋',
				'external' => '🌐',
				'voicemail' => '📧'
			];
			echo "	<td>".($type_icons[$type] ?? '')." ".escape($type_label)."</td>\n";
			
			echo "	<td><code>".escape($row['destination_number'] ?? '')."</code></td>\n";
			echo "	<td class='center'>".intval($row['ring_timeout_seconds'] ?? 30)."s</td>\n";
			
			$fallback = $row['fallback_action'] ?? 'offer_ticket';
			echo "	<td>".escape($fallback_actions[$fallback] ?? $fallback)."</td>\n";
			
			// Aliases
			$aliases = json_decode($row['aliases'] ?? '[]', true) ?: [];
			$aliases_display = implode(', ', array_slice($aliases, 0, 3));
			if (count($aliases) > 3) $aliases_display .= '...';
			echo "	<td style='font-size:0.9em;color:#666;'>".escape($aliases_display ?: '—')."</td>\n";
			
			echo "	<td class='center'>".intval($row['priority'] ?? 100)."</td>\n";
			
			// Status
			echo "	<td class='center'>\n";
			if ($row['is_enabled']) {
				echo "		<span style='background:#28a745;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;'>".($text['label-active'] ?? 'Ativo')."</span>\n";
			} else {
				echo "		<span style='background:#6c757d;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;'>".($text['label-inactive'] ?? 'Inativo')."</span>\n";
			}
			echo "	</td>\n";
			echo "</tr>\n";
			$x++;
		}
	} else {
		echo "<tr><td colspan='10' style='text-align:center;padding:20px;'>\n";
		echo "	<p>".($text['message-no_destinations'] ?? 'Nenhum destino de transferência configurado.')."</p>\n";
		echo "	<p style='color:#666;font-size:0.9em;'>".($text['message-destinations_hint'] ?? 'Adicione destinos como "Financeiro", "Atendimento", "SAC" para permitir transferências inteligentes.')."</p>\n";
		echo "</td></tr>\n";
	}

	echo "</table>\n";
	echo "<br />\n";

	// Dicas
	echo "<div style='background:#f8f9fa;border:1px solid #e9ecef;border-radius:6px;padding:15px;margin-top:10px;'>\n";
	echo "	<h4 style='margin-top:0;'>💡 Como funciona</h4>\n";
	echo "	<ul style='margin-bottom:0;'>\n";
	echo "		<li><strong>Nome:</strong> Identificação por voz (ex: \"Financeiro\", \"João do Suporte\")</li>\n";
	echo "		<li><strong>Aliases:</strong> Variações de como o cliente pode pedir (\"boletos\", \"pagamento\", \"segunda via\")</li>\n";
	echo "		<li><strong>Tipo:</strong> Ramal individual, Ring Group, Fila de CallCenter, etc.</li>\n";
	echo "		<li><strong>Fallback:</strong> O que fazer se não atender (criar ticket, voicemail, voltar ao agente)</li>\n";
	echo "	</ul>\n";
	echo "</div>\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

//include the footer
	require_once "resources/footer.php";

?>
