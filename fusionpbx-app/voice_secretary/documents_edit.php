<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Document Upload Page
	Upload documents to the knowledge base.
	⚠️ MULTI-TENANT: Uses domain_uuid from session.
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";
	require_once "resources/check_auth.php";

//check permissions
	if (permission_exists('voice_secretary_add')) {
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

//process form submission
	if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_FILES['document'])) {
		//validate token
		$token = new token;
		if (!$token->validate($_SERVER['PHP_SELF'])) {
			message::add($text['message-invalid_token'],'negative');
			header('Location: documents.php');
			exit;
		}

		$file = $_FILES['document'];
		
		//validate file
		$allowed_types = ['pdf', 'docx', 'txt', 'doc', 'md'];
		$extension = strtolower(pathinfo($file['name'], PATHINFO_EXTENSION));
		
		if (!in_array($extension, $allowed_types)) {
			message::add($text['message-invalid_file_type'] ?? 'Invalid file type', 'negative');
		} elseif ($file['error'] !== UPLOAD_ERR_OK) {
			message::add($text['message-upload_error'] ?? 'Upload error', 'negative');
		} else {
			//save file
			$document_uuid = uuid();
			$upload_dir = $_SERVER['DOCUMENT_ROOT'] . '/app/voice_secretary/uploads/' . $domain_uuid;
			
			if (!is_dir($upload_dir)) {
				mkdir($upload_dir, 0755, true);
			}
			
			$filename = $document_uuid . '.' . $extension;
			$filepath = $upload_dir . '/' . $filename;
			
			if (move_uploaded_file($file['tmp_name'], $filepath)) {
				//insert into database using direct SQL
				$sql = "INSERT INTO v_voice_documents (
					voice_document_uuid,
					domain_uuid,
					document_name,
					document_type,
					file_path,
					file_size,
					processing_status,
					enabled,
					insert_date
				) VALUES (
					:document_uuid,
					:domain_uuid,
					:document_name,
					:document_type,
					:file_path,
					:file_size,
					:processing_status,
					:enabled,
					NOW()
				)";
				
				$parameters = [];
				$parameters['document_uuid'] = $document_uuid;
				$parameters['domain_uuid'] = $domain_uuid;
				$parameters['document_name'] = trim($_POST['document_name']) ?: $file['name'];
				$parameters['document_type'] = $extension;
				$parameters['file_path'] = $filepath;
				$parameters['file_size'] = $file['size'];
				$parameters['processing_status'] = 'pending';
				$parameters['enabled'] = 'true';
				
				$database = new database;
				$database->execute($sql, $parameters);
				
				//verify insert worked
				$sql_check = "SELECT voice_document_uuid FROM v_voice_documents WHERE voice_document_uuid = :uuid";
				$params_check = ['uuid' => $document_uuid];
				$result = $database->select($sql_check, $params_check, 'row');
				
				if ($result) {
					//trigger async processing (optional, may fail silently)
					$service_url = $_ENV['VOICE_AI_SERVICE_URL'] ?? 'http://127.0.0.1:8100/api/v1';
					$payload = json_encode([
						'domain_uuid' => $domain_uuid,
						'document_uuid' => $document_uuid,
						'file_path' => $filepath,
					]);
					
					$ch = curl_init($service_url . '/documents/process');
					curl_setopt($ch, CURLOPT_POST, true);
					curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
					curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json']);
					curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
					curl_setopt($ch, CURLOPT_TIMEOUT, 5);
					curl_exec($ch);
					curl_close($ch);
					
					message::add($text['message-add']);
				} else {
					message::add('Database insert failed', 'negative');
					//cleanup uploaded file
					@unlink($filepath);
				}
				
				header('Location: documents.php');
				exit;
			} else {
				message::add($text['message-upload_error'] ?? 'Upload error: failed to move file', 'negative');
			}
		}
	}

//create token
	$object = new token;
	$token = $object->create($_SERVER['PHP_SELF']);

//include the header
	$document['title'] = $text['title-upload_document'] ?? 'Upload Document';
	require_once "resources/header.php";

//show the content
	echo "<form method='post' enctype='multipart/form-data' name='frm' id='frm'>\n";

	echo "<div class='action_bar' id='action_bar'>\n";
	echo "	<div class='heading'><b>".$document['title']."</b></div>\n";
	echo "	<div class='actions'>\n";
	echo button::create(['type'=>'button','label'=>$text['button-back'],'icon'=>$_SESSION['theme']['button_icon_back'],'id'=>'btn_back','link'=>'documents.php']);
	echo button::create(['type'=>'submit','label'=>$text['button-save'],'icon'=>'upload','id'=>'btn_save','style'=>'margin-left: 15px;']);
	echo "	</div>\n";
	echo "	<div style='clear: both;'></div>\n";
	echo "</div>\n";

	echo ($text['description-upload_document'] ?? 'Upload a document to the knowledge base.')."\n";
	echo "<br /><br />\n";

	echo "<table width='100%' border='0' cellpadding='0' cellspacing='0'>\n";

	echo "<tr>\n";
	echo "	<td width='30%' class='vncell' valign='top' align='left' nowrap='nowrap'>".($text['label-document_name'] ?? 'Document Name')."</td>\n";
	echo "	<td width='70%' class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='text' name='document_name' maxlength='255' placeholder='".($text['placeholder-document_name'] ?? 'Optional name for the document')."'>\n";
	echo "		<br />".($text['description-document_name'] ?? 'Leave blank to use the filename.')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "<tr>\n";
	echo "	<td class='vncellreq' valign='top' align='left' nowrap='nowrap'>".($text['label-file'] ?? 'File')."</td>\n";
	echo "	<td class='vtable' align='left'>\n";
	echo "		<input class='formfld' type='file' name='document' accept='.pdf,.docx,.txt,.doc,.md' required>\n";
	echo "		<br />".($text['description-file'] ?? 'Allowed formats: PDF, DOCX, TXT, DOC, MD')."\n";
	echo "	</td>\n";
	echo "</tr>\n";

	echo "</table>\n";
	echo "<br />\n";

	echo "<input type='hidden' name='".$token['name']."' value='".$token['hash']."'>\n";

	echo "</form>\n";

//include the footer
	require_once "resources/footer.php";

?>
