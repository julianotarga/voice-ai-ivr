<?php
/**
 * Voice Secretary - Transfer Rule Edit Page
 * 
 * Create or edit transfer rules.
 * ⚠️ MULTI-TENANT: Uses domain_uuid from session.
 *
 * @package voice_secretary
 */

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";
	require_once "resources/check_auth.php";

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
		echo "Error: domain_uuid not found in session.";
		exit;
	}

	$database = new database;

$action = 'add';
$data = [];

// Check if editing existing
if (isset($_GET['id']) && !empty($_GET['id'])) {
    $action = 'edit';
    $rule_uuid = $_GET['id'];
    
    $sql = "SELECT * FROM v_voice_transfer_rules WHERE transfer_rule_uuid = :uuid AND domain_uuid = :domain_uuid";
    $params = ['uuid' => $rule_uuid, 'domain_uuid' => $domain_uuid];
    $rows = $database->select($sql, $params);
    
    if (!$rows) {
        $_SESSION['message'] = $text['message-rule_not_found'];
        header('Location: transfer_rules.php');
        exit;
    }
    $data = $rows[0];
}

// Process form submission
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {
    $keywords = array_filter(array_map('trim', explode(',', $_POST['keywords'] ?? '')));
    
    $form_data = [
        'department_name' => $_POST['department_name'] ?? '',
        'keywords' => json_encode($keywords),
        'transfer_extension' => $_POST['transfer_extension'] ?? '',
        'voice_secretary_uuid' => $_POST['voice_secretary_uuid'] ?: null,
        'priority' => intval($_POST['priority'] ?? 10),
        'is_active' => isset($_POST['is_active']),
    ];
    
    if (empty($form_data['department_name']) || empty($form_data['transfer_extension'])) {
        $_SESSION['message'] = $text['message-required_fields'];
    } else {
        if ($action === 'add') {
            $sql = "INSERT INTO v_voice_transfer_rules (
                transfer_rule_uuid, domain_uuid, department_name, keywords,
                transfer_extension, voice_secretary_uuid, priority, is_active, created_at
            ) VALUES (
                :uuid, :domain_uuid, :department_name, :keywords,
                :transfer_extension, :voice_secretary_uuid, :priority, :is_active, NOW()
            )";
            $form_data['uuid'] = uuid();
            $form_data['domain_uuid'] = $domain_uuid;
        } else {
            $sql = "UPDATE v_voice_transfer_rules SET 
                department_name = :department_name, keywords = :keywords,
                transfer_extension = :transfer_extension, voice_secretary_uuid = :voice_secretary_uuid,
                priority = :priority, is_active = :is_active, updated_at = NOW()
                WHERE transfer_rule_uuid = :uuid AND domain_uuid = :domain_uuid";
            $form_data['uuid'] = $rule_uuid;
            $form_data['domain_uuid'] = $domain_uuid;
        }
        
        $database->execute($sql, $form_data);
        $_SESSION['message'] = ($action === 'add') ? $text['message-rule_created'] : $text['message-rule_updated'];
        header('Location: transfer_rules.php');
        exit;
    }
}

// Get secretaries for dropdown
$sql = "SELECT voice_secretary_uuid, secretary_name FROM v_voice_secretaries WHERE domain_uuid = :domain_uuid ORDER BY secretary_name";
$params = ['domain_uuid' => $domain_uuid];
$secretaries = $database->select($sql, $params);

// Include header
$document['title'] = ($action === 'add') ? $text['title-add_rule'] : $text['title-edit_rule'];
require_once "resources/header.php";
?>

<form method="post">
    <div class="action_bar" id="action_bar">
        <div class="heading">
            <b><?php echo ($action === 'add') ? $text['title-add_rule'] : $text['title-edit_rule']; ?></b>
        </div>
        <div class="actions">
            <button type="submit" name="submit" class="btn btn-primary btn-sm">
                <span class="fas fa-save fa-fw"></span>
                <?php echo $text['button-save']; ?>
            </button>
            <button type="button" onclick="window.location='transfer_rules.php'" class="btn btn-default btn-sm">
                <span class="fas fa-times fa-fw"></span>
                <?php echo $text['button-back']; ?>
            </button>
        </div>
        <div style="clear: both;"></div>
    </div>

    <table class="form_table">
        <tr>
            <td class="vncellreq"><?php echo $text['label-department']; ?></td>
            <td class="vtable">
                <input type="text" name="department_name" class="formfld" 
                    value="<?php echo escape($data['department_name'] ?? ''); ?>" required>
                <br><span class="description"><?php echo $text['description-department']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-keywords']; ?></td>
            <td class="vtable">
                <?php 
                $keywords = isset($data['keywords']) ? json_decode($data['keywords'], true) : [];
                ?>
                <textarea name="keywords" class="formfld" rows="3" required><?php echo escape(implode(', ', $keywords)); ?></textarea>
                <br><span class="description"><?php echo $text['description-keywords']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-extension']; ?></td>
            <td class="vtable">
                <input type="text" name="transfer_extension" class="formfld" 
                    value="<?php echo escape($data['transfer_extension'] ?? ''); ?>" required>
                <br><span class="description"><?php echo $text['description-extension']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-secretary']; ?></td>
            <td class="vtable">
                <select name="voice_secretary_uuid" class="formfld">
                    <option value=""><?php echo $text['option-all']; ?></option>
                    <?php foreach ($secretaries as $s) { ?>
                        <option value="<?php echo $s['voice_secretary_uuid']; ?>" 
                            <?php echo (($data['voice_secretary_uuid'] ?? '') === $s['voice_secretary_uuid']) ? 'selected' : ''; ?>>
                            <?php echo escape($s['secretary_name']); ?>
                        </option>
                    <?php } ?>
                </select>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-priority']; ?></td>
            <td class="vtable">
                <input type="number" name="priority" class="formfld" min="1" max="100"
                    value="<?php echo intval($data['priority'] ?? 10); ?>">
                <br><span class="description"><?php echo $text['description-priority']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-status']; ?></td>
            <td class="vtable">
                <input type="checkbox" name="is_active" <?php echo (!isset($data['is_active']) || $data['is_active']) ? 'checked' : ''; ?>>
                <?php echo $text['label-active']; ?>
            </td>
        </tr>
    </table>
</form>

<?php
// Include footer
require_once "resources/footer.php";
?>
