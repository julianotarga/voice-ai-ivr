<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Conversation Detail Page
	Shows full transcript of a conversation.
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

//get conversation
	if (!isset($_GET['id']) || !is_uuid($_GET['id'])) {
		message::add($text['message-invalid_id'] ?? 'Invalid ID', 'negative');
		header('Location: conversations.php');
		exit;
	}

	$conversation_uuid = $_GET['id'];
	$database = new database;

	$sql = "SELECT c.*, s.secretary_name, s.company_name
			FROM v_voice_conversations c
			LEFT JOIN v_voice_secretaries s ON s.voice_secretary_uuid = c.voice_secretary_uuid
			WHERE c.voice_conversation_uuid = :uuid AND c.domain_uuid = :domain_uuid";
	$parameters['uuid'] = $conversation_uuid;
	$parameters['domain_uuid'] = $domain_uuid;
	$rows = $database->select($sql, $parameters, 'all');
	unset($parameters);

	if (!$rows) {
		message::add($text['message-conversation_not_found'] ?? 'Conversation not found', 'negative');
		header('Location: conversations.php');
		exit;
	}

	$conversation = $rows[0];

//get messages
	$sql_msg = "SELECT * FROM v_voice_messages 
				WHERE voice_conversation_uuid = :uuid AND domain_uuid = :domain_uuid 
				ORDER BY sequence_number ASC";
	$parameters['uuid'] = $conversation_uuid;
	$parameters['domain_uuid'] = $domain_uuid;
	$messages = $database->select($sql_msg, $parameters, 'all') ?: [];
	unset($parameters);

//include the header
	$document['title'] = $text['title-conversation_detail'] ?? 'Conversation Detail';
	require_once "resources/header.php";

//show the content
	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'button','label'=>$text['button-back'],'icon'=>$_SESSION['theme']['button_icon_back'],'id'=>'btn_back','link'=>'conversations.php']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo "<br />\n";

	//conversation info
	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";
	
	echo "<tr>\n";
	echo "	<td width='30%' class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-date'] ?? 'Date')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>".date('d/m/Y H:i:s', strtotime($conversation['insert_date'] ?? $conversation['created_at'] ?? 'now'))."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-caller_id'] ?? 'Caller ID')."</td>\n";
	echo "	<td class='vtable' align='left'>".escape($conversation['caller_id'] ?? '—')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-secretary'] ?? 'Secretary')."</td>\n";
	echo "	<td class='vtable' align='left'>".escape($conversation['secretary_name'] ?? '—')."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-duration'] ?? 'Duration')."</td>\n";
	$duration = intval($conversation['duration_seconds'] ?? 0);
	$mins = floor($duration / 60);
	$secs = $duration % 60;
	echo "	<td class='vtable' align='left'>".sprintf('%d min %d s', $mins, $secs)."</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-action'] ?? 'Final Action')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	$action = $conversation['final_action'] ?? '';
	if ($action === 'transfer') {
		echo "		<span style='background:#17a2b8;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;'>".($text['label-transferred'] ?? 'Transferred')." → ".escape($conversation['transfer_target'] ?? '')."</span>\n";
	} elseif ($action === 'hangup') {
		echo "		<span style='background:#28a745;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;'>".($text['label-resolved'] ?? 'Resolved')."</span>\n";
	} else {
		echo "		<span style='background:#6c757d;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;'>".escape($action ?: '—')."</span>\n";
	}
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "<br />\n";

	//transcript
	echo "<div class='heading'><b>".($text['label-transcript'] ?? 'Transcript')."</b></div>\n";
	echo "<br />\n";

	if (is_array($messages) && count($messages) > 0) {
		echo "<div style='max-width: 800px;'>\n";
		foreach ($messages as $msg) {
			$is_user = ($msg['role'] === 'user');
			$bg = $is_user ? '#e3f2fd' : '#f5f5f5';
			$margin = $is_user ? 'margin-left: 50px;' : 'margin-right: 50px;';
			
			echo "<div style='padding: 15px; margin-bottom: 10px; border-radius: 10px; background: ".$bg."; ".$margin."'>\n";
			echo "	<div style='font-size: 12px; color: #666; margin-bottom: 5px;'>\n";
			if ($is_user) {
				echo "		<i class='fas fa-user'></i> ".($text['label-caller'] ?? 'Caller');
			} else {
				echo "		<i class='fas fa-robot'></i> ".($text['label-ai'] ?? 'AI');
			}
			echo " — ".date('H:i:s', strtotime($msg['created_at'] ?? $msg['insert_date'] ?? 'now'))."\n";
			echo "	</div>\n";
			echo "	<div>".nl2br(escape($msg['content'] ?? ''))."</div>\n";
			
			if (!empty($msg['audio_file'])) {
				echo "	<div style='margin-top: 10px;'>\n";
				echo "		<audio controls style='height: 30px;'>\n";
				echo "			<source src='".escape($msg['audio_file'])."' type='audio/wav'>\n";
				echo "		</audio>\n";
				echo "	</div>\n";
			}
			
			if (!empty($msg['detected_intent'])) {
				$confidence = floatval($msg['intent_confidence'] ?? 0) * 100;
				echo "	<div style='margin-top: 5px; font-size: 11px; color: #888;'>\n";
				echo "		<i class='fas fa-tag'></i> Intent: ".escape($msg['detected_intent'])." (".number_format($confidence, 1)."%)\n";
				echo "	</div>\n";
			}
			
			echo "</div>\n";
		}
		echo "</div>\n";
	} else {
		echo "<p style='color: #999;'>".($text['message-no_messages'] ?? 'No messages in this conversation.')."</p>\n";
	}

//include the footer
	require_once "resources/footer.php";

?>
