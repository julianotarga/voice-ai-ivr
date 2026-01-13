<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Conversations History
	Lists all conversation history.
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

//get filters
	$filter_secretary = $_GET['secretary'] ?? '';
	$filter_action = $_GET['action'] ?? '';
	$filter_date_from = $_GET['date_from'] ?? '';
	$filter_date_to = $_GET['date_to'] ?? '';

//build query
	$database = new database;
	$sql = "SELECT c.*, s.secretary_name ";
	$sql .= "FROM v_voice_conversations c ";
	$sql .= "LEFT JOIN v_voice_secretaries s ON s.voice_secretary_uuid = c.voice_secretary_uuid ";
	$sql .= "WHERE c.domain_uuid = :domain_uuid ";
	$parameters['domain_uuid'] = $domain_uuid;

	if (!empty($filter_secretary)) {
		$sql .= "AND c.voice_secretary_uuid = :secretary ";
		$parameters['secretary'] = $filter_secretary;
	}

	if (!empty($filter_action)) {
		$sql .= "AND c.final_action = :action ";
		$parameters['action'] = $filter_action;
	}

	if (!empty($filter_date_from)) {
		$sql .= "AND c.insert_date >= :date_from ";
		$parameters['date_from'] = $filter_date_from . ' 00:00:00';
	}

	if (!empty($filter_date_to)) {
		$sql .= "AND c.insert_date <= :date_to ";
		$parameters['date_to'] = $filter_date_to . ' 23:59:59';
	}

	$sql .= "ORDER BY c.insert_date DESC LIMIT 100";
	$conversations = $database->select($sql, $parameters, 'all') ?: [];
	unset($sql, $parameters);
	$num_rows = count($conversations);

//get secretaries for filter dropdown
	$sql_sec = "SELECT voice_secretary_uuid, secretary_name FROM v_voice_secretaries WHERE domain_uuid = :domain_uuid ORDER BY secretary_name";
	$secretaries = $database->select($sql_sec, ['domain_uuid' => $domain_uuid], 'all') ?: [];
	unset($sql_sec);

//include the header
	$document['title'] = $text['title-conversations'] ?? 'Conversations';
	require_once "resources/header.php";

//include tab navigation
	$current_page = 'conversations';
	require_once "resources/nav_tabs.php";

//show the content
	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".($text['title-conversations'] ?? 'Conversations')."</b><div class='count'>".number_format($num_rows)."</div></div>\n";
	echo "	<div class='actions'>\n";
	
	//search form
	echo "		<form id='form_search' class='inline' method='get'>\n";
	echo "			<select name='secretary' class='formfld' style='width: auto; margin-right: 5px;'>\n";
	echo "				<option value=''>".($text['option-all_secretaries'] ?? 'All Secretaries')."</option>\n";
	foreach ($secretaries as $s) {
		$selected = ($filter_secretary === $s['voice_secretary_uuid']) ? 'selected' : '';
		echo "				<option value='".escape($s['voice_secretary_uuid'])."' ".$selected.">".escape($s['secretary_name'])."</option>\n";
	}
	echo "			</select>\n";
	echo "			<input type='date' name='date_from' class='formfld' style='width: 130px; margin-right: 5px;' value='".escape($filter_date_from)."' placeholder='From'>\n";
	echo "			<input type='date' name='date_to' class='formfld' style='width: 130px; margin-right: 5px;' value='".escape($filter_date_to)."' placeholder='To'>\n";
	echo button::create(['label'=>$text['button-search'],'icon'=>$_SESSION['theme']['button_icon_search'],'type'=>'submit','id'=>'btn_search']);
	if (!empty($filter_secretary) || !empty($filter_date_from) || !empty($filter_date_to)) {
		echo button::create(['label'=>$text['button-reset'] ?? 'Reset','icon'=>'undo','type'=>'button','link'=>'conversations.php']);
	}
	echo "		</form>\n";
	
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-conversations'] ?? 'View conversation history and transcripts.')."\n";
	echo "<br /><br />\n";

	echo "<div class='card'>\n";
	echo "<table class='list'>\n";
	echo "<tr class='list-header'>\n";
	echo "<th>".($text['label-date'] ?? 'Date')."</th>\n";
	echo "<th>".($text['label-secretary'] ?? 'Secretary')."</th>\n";
	echo "<th>".($text['label-caller'] ?? 'Caller')."</th>\n";
	echo "<th class='center'>".($text['label-duration'] ?? 'Duration')."</th>\n";
	echo "<th class='center'>".($text['label-turns'] ?? 'Turns')."</th>\n";
	echo "<th class='center'>".($text['label-action'] ?? 'Action')."</th>\n";
	echo "<th class='hide-sm-dn'>".($text['label-ticket'] ?? 'Ticket')."</th>\n";
	if (permission_exists('voice_secretary_edit')) {
		echo "<td class='action-button'>&nbsp;</td>\n";
	}
	echo "</tr>\n";

	if (is_array($conversations) && @sizeof($conversations) != 0) {
		foreach($conversations as $row) {
			$list_row_url = "conversation_detail.php?id=".urlencode($row['voice_conversation_uuid']);
			echo "<tr class='list-row' href='".$list_row_url."'>\n";
			echo "	<td>".(!empty($row['insert_date']) ? date('d/m/Y H:i', strtotime($row['insert_date'])) : '')."</td>\n";
			echo "	<td>".escape($row['secretary_name'] ?? '')."</td>\n";
			echo "	<td>".escape($row['caller_id_number'] ?? '')."</td>\n";
			
			// Calculate duration
			$duration = '';
			if (!empty($row['start_time']) && !empty($row['end_time'])) {
				$start = strtotime($row['start_time']);
				$end = strtotime($row['end_time']);
				$diff = $end - $start;
				$duration = gmdate('i:s', $diff);
			}
			echo "	<td class='center'>".$duration."</td>\n";
			echo "	<td class='center'>".intval($row['total_turns'] ?? 0)."</td>\n";
			
			// Action badge
			$action = $row['final_action'] ?? '';
			$action_badges = [
				'transfer' => 'badge-success',
				'hangup' => 'badge-secondary',
				'voicemail' => 'badge-info',
				'ticket' => 'badge-warning'
			];
			echo "	<td class='center'>";
			if (!empty($action)) {
				echo "<span class='badge ".($action_badges[$action] ?? 'badge-secondary')."'>".ucfirst($action)."</span>";
			}
			echo "</td>\n";
			
			echo "	<td class='hide-sm-dn'>";
			if ($row['ticket_created'] ?? false) {
				echo "<span class='badge badge-success'>".escape($row['ticket_id'] ?? 'Yes')."</span>";
			}
			echo "</td>\n";
			
			if (permission_exists('voice_secretary_edit')) {
				echo "	<td class='action-button'>";
				echo button::create(['type'=>'button','title'=>$text['button-view'] ?? 'View','icon'=>'eye','link'=>$list_row_url]);
				echo "	</td>\n";
			}
			echo "</tr>\n";
		}
	}
	else {
		echo "<tr><td colspan='8' class='no-results-found'>".($text['message-no_records'] ?? 'No records found.')."</td></tr>\n";
	}

	echo "</table>\n";
	echo "</div>\n";
	echo "<br />\n";

//include the footer
	require_once "resources/footer.php";

?>
