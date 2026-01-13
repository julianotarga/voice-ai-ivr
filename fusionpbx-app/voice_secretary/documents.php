<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Documents List
	Lists all documents in the knowledge base.
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

//get posted data
	if (!empty($_POST['documents']) && is_array($_POST['documents'])) {
		$action = $_POST['action'] ?? '';
		$documents_list = $_POST['documents'];
	}

//process the http post data by action
	if (!empty($action) && !empty($documents_list) && is_array($documents_list) && @sizeof($documents_list) != 0) {

		//validate the token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: documents.php');
			exit;
		}

		switch ($action) {
			case 'delete':
				if (permission_exists('voice_secretary_delete')) {
					$database = new database;
					foreach ($documents_list as $doc) {
						if (!empty($doc['uuid']) && is_uuid($doc['uuid'])) {
							$array['voice_documents'][]['voice_document_uuid'] = $doc['uuid'];
						}
					}
					if (!empty($array)) {
						$database->app_name = 'voice_secretary';
						$database->app_uuid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
						$database->delete($array);
						unset($array);
					}
				}
				break;
		}

		header('Location: documents.php');
		exit;
	}

//get documents
	$database = new database;
	$sql = "SELECT * FROM v_voice_documents ";
	$sql .= "WHERE domain_uuid = :domain_uuid ";
	$sql .= "ORDER BY insert_date DESC";
	$parameters['domain_uuid'] = $domain_uuid;
	$documents = $database->select($sql, $parameters, 'all') ?: [];
	unset($sql, $parameters);
	$num_rows = count($documents);

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//helper function
	function format_file_size($bytes) {
		if ($bytes >= 1048576) {
			return number_format($bytes / 1048576, 2) . ' MB';
		} elseif ($bytes >= 1024) {
			return number_format($bytes / 1024, 2) . ' KB';
		}
		return $bytes . ' B';
	}

//include the header
	$document['title'] = $text['title-voice_documents'] ?? 'Documents';
	require_once "resources/header.php";

//include tab navigation
	$current_page = 'documents';
	require_once "resources/nav_tabs.php";

//show the content
	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".($text['title-voice_documents'] ?? 'Documents')."</b><div class='count'>".number_format($num_rows)."</div></div>\n";
	echo "	<div class='actions'>\n";
	if (permission_exists('voice_secretary_add')) {
		echo button::create(['type'=>'button','label'=>$text['button-upload'] ?? 'Upload','icon'=>'upload','id'=>'btn_add','link'=>'documents_edit.php']);
	}
	if (permission_exists('voice_secretary_delete') && $documents) {
		echo button::create(['type'=>'button','label'=>$text['button-delete'],'icon'=>$_SESSION['theme']['button_icon_delete'],'id'=>'btn_delete','name'=>'btn_delete','style'=>'display: none;','onclick'=>"modal_open('modal-delete','btn_delete');"]);
	}
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	if (permission_exists('voice_secretary_delete') && $documents) {
		echo modal::create(['id'=>'modal-delete','type'=>'delete','actions'=>button::create(['type'=>'button','label'=>$text['button-continue'],'icon'=>'check','id'=>'btn_delete','style'=>'float: right; margin-left: 15px;','collapse'=>'never','onclick'=>"modal_close(); list_action_set('delete'); list_form_submit('form_list');"])]);
	}

	echo ($text['description-voice_documents'] ?? 'Upload documents to create a knowledge base for the AI assistant.')."\n";
	echo "<br /><br />\n";

	echo "<form id='form_list' method='post'>\n";
	echo "<input type='hidden' id='action' name='action' value=''>\n";

	echo "<div class='card'>\n";
	echo "<table class='list'>\n";
	echo "<tr class='list-header'>\n";
	if (permission_exists('voice_secretary_delete')) {
		echo "	<th class='checkbox'>\n";
		echo "		<input type='checkbox' id='checkbox_all' name='checkbox_all' onclick='list_all_toggle(); checkbox_on_change(this);' ".(empty($documents) ? "style='visibility: hidden;'" : null).">\n";
		echo "	</th>\n";
	}
	echo "<th>".($text['label-document_name'] ?? 'Document Name')."</th>\n";
	echo "<th>".($text['label-document_type'] ?? 'Type')."</th>\n";
	echo "<th class='hide-sm-dn'>".($text['label-file_size'] ?? 'Size')."</th>\n";
	echo "<th class='center'>".($text['label-chunks'] ?? 'Chunks')."</th>\n";
	echo "<th class='center'>".($text['label-status'] ?? 'Status')."</th>\n";
	echo "<th class='hide-sm-dn'>".($text['label-created'] ?? 'Created')."</th>\n";
	echo "</tr>\n";

	if (is_array($documents) && @sizeof($documents) != 0) {
		$x = 0;
		foreach($documents as $row) {
			echo "<tr class='list-row'>\n";
			if (permission_exists('voice_secretary_delete')) {
				echo "	<td class='checkbox'>\n";
				echo "		<input type='checkbox' name='documents[$x][checked]' id='checkbox_".$x."' value='true' onclick=\"checkbox_on_change(this); if (!this.checked) { document.getElementById('checkbox_all').checked = false; }\">\n";
				echo "		<input type='hidden' name='documents[$x][uuid]' value='".escape($row['voice_document_uuid'])."' />\n";
				echo "	</td>\n";
			}
			echo "	<td>".escape($row['document_name'])."</td>\n";
			echo "	<td><span class='badge badge-info'>".strtoupper(escape($row['document_type'] ?? 'txt'))."</span></td>\n";
			echo "	<td class='hide-sm-dn'>".format_file_size($row['file_size'] ?? 0)."</td>\n";
			echo "	<td class='center'>".intval($row['chunk_count'] ?? 0)."</td>\n";
			
			$status = $row['processing_status'] ?? 'pending';
			$status_badges = [
				'completed' => 'badge-success',
				'processing' => 'badge-warning',
				'failed' => 'badge-danger',
				'pending' => 'badge-secondary'
			];
			echo "	<td class='center'><span class='badge ".($status_badges[$status] ?? 'badge-secondary')."'>".ucfirst($status)."</span></td>\n";
			
			echo "	<td class='hide-sm-dn'>".(!empty($row['insert_date']) ? date('d/m/Y H:i', strtotime($row['insert_date'])) : '')."</td>\n";
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
