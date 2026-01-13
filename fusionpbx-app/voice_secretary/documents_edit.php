<?php
/**
 * Voice Secretary - Document Upload Page
 * 
 * Upload documents to the knowledge base.
 * ⚠️ MULTI-TENANT: Uses domain_uuid from session.
 *
 * @package voice_secretary
 */

// Include required files
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";

// Check permission
if (permission_exists('voice_secretary_add')) {
    // Access allowed
} else {
    echo "access denied";
    exit;
}

// Validate multi-tenant
require_once "resources/classes/domain_validator.php";
domain_validator::init();

$domain_uuid = domain_validator::require_domain_uuid();

// Process form submission
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_FILES['document'])) {
    $file = $_FILES['document'];
    
    // Validate file
    $allowed_types = ['pdf', 'docx', 'txt', 'doc', 'md'];
    $extension = strtolower(pathinfo($file['name'], PATHINFO_EXTENSION));
    
    if (!in_array($extension, $allowed_types)) {
        $_SESSION['message'] = $text['message-invalid_file_type'];
    } elseif ($file['error'] !== UPLOAD_ERR_OK) {
        $_SESSION['message'] = $text['message-upload_error'];
    } else {
        // Save file
        $document_uuid = uuid();
        $upload_dir = $_SERVER['DOCUMENT_ROOT'] . '/app/voice_secretary/uploads/' . $domain_uuid;
        
        if (!is_dir($upload_dir)) {
            mkdir($upload_dir, 0755, true);
        }
        
        $filename = $document_uuid . '.' . $extension;
        $filepath = $upload_dir . '/' . $filename;
        
        if (move_uploaded_file($file['tmp_name'], $filepath)) {
            // Insert into database
            $database = database::new();
            
            $sql = "INSERT INTO v_voice_documents (
                document_uuid,
                domain_uuid,
                document_name,
                file_path,
                file_type,
                file_size,
                processing_status,
                created_at
            ) VALUES (
                :document_uuid,
                :domain_uuid,
                :document_name,
                :file_path,
                :file_type,
                :file_size,
                'pending',
                NOW()
            )";
            
            $parameters = [
                'document_uuid' => $document_uuid,
                'domain_uuid' => $domain_uuid,
                'document_name' => $_POST['document_name'] ?: $file['name'],
                'file_path' => $filepath,
                'file_type' => $extension,
                'file_size' => $file['size'],
            ];
            
            $database->execute($sql, $parameters);
            
            // Trigger async processing
            // Default alinhado ao docker-compose: voice-ai-service expõe 8100
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
            curl_setopt($ch, CURLOPT_TIMEOUT, 5); // Quick timeout, processing is async
            curl_exec($ch);
            curl_close($ch);
            
            $_SESSION['message'] = $text['message-document_uploaded'];
            header('Location: documents.php');
            exit;
        } else {
            $_SESSION['message'] = $text['message-upload_error'];
        }
    }
}

// Include header
$document['title'] = $text['title-upload_document'];
require_once "resources/header.php";
?>

<form method="post" enctype="multipart/form-data">
    <div class="action_bar" id="action_bar">
        <div class="heading">
            <b><?php echo $text['title-upload_document']; ?></b>
        </div>
        <div class="actions">
            <button type="submit" class="btn btn-primary btn-sm">
                <span class="fas fa-upload fa-fw"></span>
                <?php echo $text['button-upload']; ?>
            </button>
            <button type="button" onclick="window.location='documents.php'" class="btn btn-default btn-sm">
                <span class="fas fa-times fa-fw"></span>
                <?php echo $text['button-back']; ?>
            </button>
        </div>
        <div style="clear: both;"></div>
    </div>

    <table class="form_table">
        <tr>
            <td class="vncell"><?php echo $text['label-document_name']; ?></td>
            <td class="vtable">
                <input type="text" name="document_name" class="formfld" placeholder="<?php echo $text['placeholder-document_name']; ?>">
                <br><span class="description"><?php echo $text['description-document_name']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-file']; ?></td>
            <td class="vtable">
                <input type="file" name="document" class="formfld" accept=".pdf,.docx,.txt,.doc,.md" required>
                <br><span class="description"><?php echo $text['description-file']; ?></span>
            </td>
        </tr>
    </table>
</form>

<?php
// Include footer
require_once "resources/footer.php";
?>
