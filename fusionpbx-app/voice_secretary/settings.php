<?php
/**
 * Voice Secretary - Settings Page
 * 
 * Global settings for voice AI.
 * ⚠️ MULTI-TENANT: Uses domain_uuid from session.
 *
 * @package voice_secretary
 */

// Include required files
require_once dirname(__DIR__, 2) . "/resources/require.php";
require_once "resources/check_auth.php";

// Check permission
if (permission_exists('voice_secretary_edit')) {
    // Access allowed
} else {
    echo "access denied";
    exit;
}

// Validate multi-tenant
require_once "resources/classes/domain_validator.php";
domain_validator::init();

$domain_uuid = domain_validator::require_domain_uuid();
$database = database::new();

// Get current settings
$sql = "SELECT setting_name, setting_value FROM v_default_settings 
        WHERE domain_uuid = :domain_uuid 
        AND default_setting_category = 'voice_secretary'";
$params = ['domain_uuid' => $domain_uuid];
$rows = $database->select($sql, $params);

$settings = [];
foreach ($rows as $row) {
    $settings[$row['setting_name']] = $row['setting_value'];
}

// Process form submission
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {
    $new_settings = [
        // Default alinhado ao docker-compose: voice-ai-service expõe 8100
        'service_url' => $_POST['service_url'] ?? 'http://127.0.0.1:8100/api/v1',
        'data_retention_days' => intval($_POST['data_retention_days'] ?? 90),
        'omniplay_webhook_url' => $_POST['omniplay_webhook_url'] ?? '',
        'omniplay_api_key' => $_POST['omniplay_api_key'] ?? '',
        'max_concurrent_calls' => intval($_POST['max_concurrent_calls'] ?? 10),
        'default_max_turns' => intval($_POST['default_max_turns'] ?? 20),
        'recording_enabled' => isset($_POST['recording_enabled']) ? 'true' : 'false',
    ];
    
    foreach ($new_settings as $name => $value) {
        // Check if exists
        $sql_check = "SELECT count(*) as cnt FROM v_default_settings 
                      WHERE domain_uuid = :domain_uuid 
                      AND default_setting_category = 'voice_secretary' 
                      AND setting_name = :name";
        $check = $database->select($sql_check, [
            'domain_uuid' => $domain_uuid,
            'name' => $name
        ]);
        
        if ($check[0]['cnt'] > 0) {
            // Update
            $sql_upd = "UPDATE v_default_settings SET setting_value = :value 
                        WHERE domain_uuid = :domain_uuid 
                        AND default_setting_category = 'voice_secretary' 
                        AND setting_name = :name";
        } else {
            // Insert
            $sql_upd = "INSERT INTO v_default_settings 
                        (default_setting_uuid, domain_uuid, default_setting_category, setting_name, setting_value, default_setting_enabled)
                        VALUES (uuid_generate_v4(), :domain_uuid, 'voice_secretary', :name, :value, true)";
        }
        
        $database->execute($sql_upd, [
            'domain_uuid' => $domain_uuid,
            'name' => $name,
            'value' => $value
        ]);
    }
    
    $_SESSION['message'] = $text['message-settings_saved'];
    header('Location: settings.php');
    exit;
}

// Include header
$document['title'] = $text['title-settings'];
require_once "resources/header.php";
?>

<form method="post">
    <div class="action_bar" id="action_bar">
        <div class="heading">
            <b><?php echo $text['title-settings']; ?></b>
        </div>
        <div class="actions">
            <button type="submit" name="submit" class="btn btn-primary btn-sm">
                <span class="fas fa-save fa-fw"></span>
                <?php echo $text['button-save']; ?>
            </button>
        </div>
        <div style="clear: both;"></div>
    </div>

    <table class="form_table">
        <!-- Service Configuration -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-service']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-service_url']; ?></td>
            <td class="vtable">
                <input type="url" name="service_url" class="formfld" 
                    value="<?php echo escape($settings['service_url'] ?? 'http://127.0.0.1:8100/api/v1'); ?>">
                <br><span class="description"><?php echo $text['description-service_url']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-max_concurrent']; ?></td>
            <td class="vtable">
                <input type="number" name="max_concurrent_calls" class="formfld" min="1" max="100"
                    value="<?php echo intval($settings['max_concurrent_calls'] ?? 10); ?>">
                <br><span class="description"><?php echo $text['description-max_concurrent']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-default_max_turns']; ?></td>
            <td class="vtable">
                <input type="number" name="default_max_turns" class="formfld" min="1" max="100"
                    value="<?php echo intval($settings['default_max_turns'] ?? 20); ?>">
            </td>
        </tr>
        
        <!-- Data Management -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-data']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-retention_days']; ?></td>
            <td class="vtable">
                <input type="number" name="data_retention_days" class="formfld" min="1" max="365"
                    value="<?php echo intval($settings['data_retention_days'] ?? 90); ?>">
                <br><span class="description"><?php echo $text['description-retention']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-recording']; ?></td>
            <td class="vtable">
                <input type="checkbox" name="recording_enabled" 
                    <?php echo (($settings['recording_enabled'] ?? 'false') === 'true') ? 'checked' : ''; ?>>
                <?php echo $text['description-recording']; ?>
            </td>
        </tr>
        
        <!-- OmniPlay Integration -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-omniplay']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-webhook_url']; ?></td>
            <td class="vtable">
                <input type="url" name="omniplay_webhook_url" class="formfld" 
                    value="<?php echo escape($settings['omniplay_webhook_url'] ?? ''); ?>" 
                    placeholder="https://omniplay.example.com/webhook/voice-ai">
                <br><span class="description"><?php echo $text['description-omniplay_webhook']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-api_key']; ?></td>
            <td class="vtable">
                <input type="password" name="omniplay_api_key" class="formfld" 
                    value="<?php echo escape($settings['omniplay_api_key'] ?? ''); ?>">
                <br><span class="description"><?php echo $text['description-omniplay_key']; ?></span>
            </td>
        </tr>
    </table>
</form>

<?php
// Include footer
require_once "resources/footer.php";
?>
