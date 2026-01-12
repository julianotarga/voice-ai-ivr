<?php
/**
 * Voice Secretary - Edit/Create Page
 * 
 * Create or edit a voice AI secretary.
 * ⚠️ MULTI-TENANT: Uses domain_uuid from session.
 *
 * @package voice_secretary
 */

// Include required files
require_once "root.php";
require_once "resources/require.php";
require_once "resources/check_auth.php";

// Check permission
if (permission_exists('voice_secretary_add') || permission_exists('voice_secretary_edit')) {
    // Access allowed
} else {
    echo "access denied";
    exit;
}

// Include classes
require_once "resources/classes/voice_secretary.php";

// Validate multi-tenant
require_once "resources/classes/domain_validator.php";
domain_validator::init();

// Initialize
$secretary_obj = new voice_secretary();
$action = 'add';
$data = [];

// Check if editing existing
if (isset($_GET['id']) && !empty($_GET['id'])) {
    $action = 'edit';
    $secretary_uuid = $_GET['id'];
    $data = $secretary_obj->get($secretary_uuid);
    
    if (!$data) {
        $_SESSION['message'] = $text['message-secretary_not_found'];
        header('Location: secretary.php');
        exit;
    }
}

// Process form submission
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {
    // Collect form data
    $form_data = [
        'secretary_name' => $_POST['secretary_name'] ?? '',
        'company_name' => $_POST['company_name'] ?? '',
        'system_prompt' => $_POST['system_prompt'] ?? '',
        'greeting_message' => $_POST['greeting_message'] ?? '',
        'farewell_message' => $_POST['farewell_message'] ?? '',
        // Processing mode (v1/v2)
        'processing_mode' => $_POST['processing_mode'] ?? 'turn_based',
        'realtime_provider_uuid' => $_POST['realtime_provider_uuid'] ?? null,
        'extension' => $_POST['extension'] ?? '',
        // Turn-based providers
        'stt_provider_uuid' => $_POST['stt_provider_uuid'] ?? null,
        'tts_provider_uuid' => $_POST['tts_provider_uuid'] ?? null,
        'llm_provider_uuid' => $_POST['llm_provider_uuid'] ?? null,
        'embeddings_provider_uuid' => $_POST['embeddings_provider_uuid'] ?? null,
        'tts_voice' => $_POST['tts_voice'] ?? '',
        'language' => $_POST['language'] ?? 'pt-BR',
        'max_turns' => intval($_POST['max_turns'] ?? 20),
        'transfer_extension' => $_POST['transfer_extension'] ?? '200',
        'is_active' => isset($_POST['is_active']),
        'webhook_url' => $_POST['webhook_url'] ?? '',
    ];
    
    // Validate
    if (empty($form_data['secretary_name'])) {
        $_SESSION['message'] = $text['message-name_required'];
    } else {
        try {
            if ($action === 'add') {
                $secretary_obj->create($form_data);
                $_SESSION['message'] = $text['message-secretary_created'];
            } else {
                $secretary_obj->update($secretary_uuid, $form_data);
                $_SESSION['message'] = $text['message-secretary_updated'];
            }
            header('Location: secretary.php');
            exit;
        } catch (Exception $e) {
            $_SESSION['message'] = $text['message-error'] . ': ' . $e->getMessage();
        }
    }
}

// Get providers for dropdowns
$stt_providers = $secretary_obj->get_providers('stt');
$tts_providers = $secretary_obj->get_providers('tts');
$llm_providers = $secretary_obj->get_providers('llm');
$embeddings_providers = $secretary_obj->get_providers('embeddings');
$realtime_providers = $secretary_obj->get_providers('realtime');

// Include header
$document['title'] = ($action === 'add') ? $text['title-add_secretary'] : $text['title-edit_secretary'];
require_once "resources/header.php";
?>

<form method="post" enctype="multipart/form-data">
    <!-- MULTI-TENANT: domain_uuid is from session, NOT from form -->
    
    <div class="action_bar" id="action_bar">
        <div class="heading">
            <b><?php echo ($action === 'add') ? $text['title-add_secretary'] : $text['title-edit_secretary']; ?></b>
        </div>
        <div class="actions">
            <button type="submit" name="submit" class="btn btn-primary btn-sm">
                <span class="fas fa-save fa-fw"></span>
                <?php echo $text['button-save']; ?>
            </button>
            <button type="button" onclick="window.location='secretary.php'" class="btn btn-default btn-sm">
                <span class="fas fa-times fa-fw"></span>
                <?php echo $text['button-back']; ?>
            </button>
        </div>
        <div style="clear: both;"></div>
    </div>

    <table class="form_table">
        <!-- Basic Info -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-basic_info']; ?></b></th>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-secretary_name']; ?></td>
            <td class="vtable">
                <input type="text" name="secretary_name" class="formfld" 
                    value="<?php echo escape($data['secretary_name'] ?? ''); ?>" required>
                <br><span class="description"><?php echo $text['description-secretary_name']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-company_name']; ?></td>
            <td class="vtable">
                <input type="text" name="company_name" class="formfld" 
                    value="<?php echo escape($data['company_name'] ?? ''); ?>">
                <br><span class="description"><?php echo $text['description-company_name']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-language']; ?></td>
            <td class="vtable">
                <select name="language" class="formfld">
                    <option value="pt-BR" <?php echo (($data['language'] ?? 'pt-BR') === 'pt-BR') ? 'selected' : ''; ?>>Português (Brasil)</option>
                    <option value="en-US" <?php echo (($data['language'] ?? '') === 'en-US') ? 'selected' : ''; ?>>English (US)</option>
                    <option value="es-ES" <?php echo (($data['language'] ?? '') === 'es-ES') ? 'selected' : ''; ?>>Español</option>
                </select>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-extension'] ?? 'Extension'; ?></td>
            <td class="vtable">
                <input type="text" name="extension" class="formfld" 
                    value="<?php echo escape($data['extension'] ?? ''); ?>" placeholder="8000">
                <br><span class="description"><?php echo $text['description-extension'] ?? 'Extension number for this secretary (e.g., 8000)'; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-status']; ?></td>
            <td class="vtable">
                <input type="checkbox" name="is_active" <?php echo (!isset($data['is_active']) || $data['is_active']) ? 'checked' : ''; ?>>
                <?php echo $text['label-active']; ?>
            </td>
        </tr>

        <!-- Processing Mode (v1/v2) -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-processing_mode'] ?? 'Processing Mode'; ?></b></th>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-processing_mode'] ?? 'Mode'; ?></td>
            <td class="vtable">
                <label style="margin-right: 20px;">
                    <input type="radio" name="processing_mode" value="turn_based" 
                        <?php echo (($data['processing_mode'] ?? 'turn_based') === 'turn_based') ? 'checked' : ''; ?>
                        onchange="toggleModeFields()">
                    <?php echo $text['option-turn_based'] ?? 'Turn-based (v1)'; ?>
                </label>
                <label style="margin-right: 20px;">
                    <input type="radio" name="processing_mode" value="realtime" 
                        <?php echo (($data['processing_mode'] ?? '') === 'realtime') ? 'checked' : ''; ?>
                        onchange="toggleModeFields()">
                    <?php echo $text['option-realtime'] ?? 'Realtime (v2)'; ?>
                </label>
                <label>
                    <input type="radio" name="processing_mode" value="auto" 
                        <?php echo (($data['processing_mode'] ?? '') === 'auto') ? 'checked' : ''; ?>
                        onchange="toggleModeFields()">
                    <?php echo $text['option-auto'] ?? 'Auto (realtime with fallback)'; ?>
                </label>
                <br>
                <span class="description">
                    <strong><?php echo $text['label-turn_based'] ?? 'Turn-based'; ?>:</strong> <?php echo $text['description-turn_based'] ?? 'User speaks, AI responds (traditional IVR)'; ?><br>
                    <strong><?php echo $text['label-realtime'] ?? 'Realtime'; ?>:</strong> <?php echo $text['description-realtime'] ?? 'Natural conversation with interruption support'; ?><br>
                    <strong><?php echo $text['label-auto'] ?? 'Auto'; ?>:</strong> <?php echo $text['description-auto'] ?? 'Tries realtime first, falls back to turn-based'; ?>
                </span>
            </td>
        </tr>
        <tr id="realtime_provider_row" style="display: none;">
            <td class="vncell"><?php echo $text['label-realtime_provider'] ?? 'Realtime Provider'; ?></td>
            <td class="vtable">
                <select name="realtime_provider_uuid" class="formfld">
                    <option value=""><?php echo $text['option-select'] ?? 'Select...'; ?></option>
                    <?php foreach ($realtime_providers as $p) { ?>
                        <option value="<?php echo $p['provider_uuid']; ?>" 
                            <?php echo (($data['realtime_provider_uuid'] ?? '') === $p['provider_uuid']) ? 'selected' : ''; ?>>
                            <?php echo escape($p['provider_name']); ?>
                        </option>
                    <?php } ?>
                </select>
                <br><span class="description"><?php echo $text['description-realtime_provider'] ?? 'OpenAI Realtime, ElevenLabs, Gemini, or Custom Pipeline'; ?></span>
            </td>
        </tr>
        <tr id="realtime_info_row" style="display: none;">
            <td class="vncell"><?php echo $text['label-realtime_features'] ?? 'Features'; ?></td>
            <td class="vtable">
                <ul style="margin: 0; padding-left: 20px; font-size: 12px; color: #666;">
                    <li>✓ <?php echo $text['feature-barge_in'] ?? 'Barge-in (interrupt AI response)'; ?></li>
                    <li>✓ <?php echo $text['feature-natural'] ?? 'Natural conversation flow'; ?></li>
                    <li>✓ <?php echo $text['feature-latency'] ?? 'Low latency (~300ms)'; ?></li>
                    <li>⚠️ <?php echo $text['feature-cost'] ?? 'Higher cost per minute'; ?></li>
                </ul>
            </td>
        </tr>

        <!-- Prompts -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-prompts']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-system_prompt']; ?></td>
            <td class="vtable">
                <textarea name="system_prompt" class="formfld" rows="6"><?php echo escape($data['system_prompt'] ?? ''); ?></textarea>
                <br><span class="description"><?php echo $text['description-system_prompt']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-greeting']; ?></td>
            <td class="vtable">
                <textarea name="greeting_message" class="formfld" rows="2"><?php echo escape($data['greeting_message'] ?? 'Olá! Como posso ajudar?'); ?></textarea>
                <br><span class="description"><?php echo $text['description-greeting']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-farewell']; ?></td>
            <td class="vtable">
                <textarea name="farewell_message" class="formfld" rows="2"><?php echo escape($data['farewell_message'] ?? 'Foi um prazer ajudar! Até logo!'); ?></textarea>
                <br><span class="description"><?php echo $text['description-farewell']; ?></span>
            </td>
        </tr>

        <!-- AI Providers -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-providers']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-stt_provider']; ?></td>
            <td class="vtable">
                <select name="stt_provider_uuid" class="formfld">
                    <option value=""><?php echo $text['option-default']; ?></option>
                    <?php foreach ($stt_providers as $p) { ?>
                        <option value="<?php echo $p['provider_uuid']; ?>" 
                            <?php echo (($data['stt_provider_uuid'] ?? '') === $p['provider_uuid']) ? 'selected' : ''; ?>>
                            <?php echo escape($p['provider_name']); ?>
                        </option>
                    <?php } ?>
                </select>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-tts_provider']; ?></td>
            <td class="vtable">
                <select name="tts_provider_uuid" class="formfld">
                    <option value=""><?php echo $text['option-default']; ?></option>
                    <?php foreach ($tts_providers as $p) { ?>
                        <option value="<?php echo $p['provider_uuid']; ?>" 
                            <?php echo (($data['tts_provider_uuid'] ?? '') === $p['provider_uuid']) ? 'selected' : ''; ?>>
                            <?php echo escape($p['provider_name']); ?>
                        </option>
                    <?php } ?>
                </select>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-tts_voice']; ?></td>
            <td class="vtable">
                <input type="text" name="tts_voice" class="formfld" 
                    value="<?php echo escape($data['tts_voice'] ?? ''); ?>">
                <button type="button" onclick="testVoice()" class="btn btn-default btn-xs" style="margin-left: 10px;">
                    <span class="fas fa-play fa-fw"></span> <?php echo $text['button-test_voice']; ?>
                </button>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-llm_provider']; ?></td>
            <td class="vtable">
                <select name="llm_provider_uuid" class="formfld">
                    <option value=""><?php echo $text['option-default']; ?></option>
                    <?php foreach ($llm_providers as $p) { ?>
                        <option value="<?php echo $p['provider_uuid']; ?>" 
                            <?php echo (($data['llm_provider_uuid'] ?? '') === $p['provider_uuid']) ? 'selected' : ''; ?>>
                            <?php echo escape($p['provider_name']); ?>
                        </option>
                    <?php } ?>
                </select>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-embeddings_provider']; ?></td>
            <td class="vtable">
                <select name="embeddings_provider_uuid" class="formfld">
                    <option value=""><?php echo $text['option-default']; ?></option>
                    <?php foreach ($embeddings_providers as $p) { ?>
                        <option value="<?php echo $p['provider_uuid']; ?>" 
                            <?php echo (($data['embeddings_provider_uuid'] ?? '') === $p['provider_uuid']) ? 'selected' : ''; ?>>
                            <?php echo escape($p['provider_name']); ?>
                        </option>
                    <?php } ?>
                </select>
            </td>
        </tr>

        <!-- Transfer Settings -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-transfer']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-transfer_extension']; ?></td>
            <td class="vtable">
                <input type="text" name="transfer_extension" class="formfld" 
                    value="<?php echo escape($data['transfer_extension'] ?? '200'); ?>">
                <br><span class="description"><?php echo $text['description-transfer_extension']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-max_turns']; ?></td>
            <td class="vtable">
                <input type="number" name="max_turns" class="formfld" min="1" max="100"
                    value="<?php echo escape($data['max_turns'] ?? 20); ?>">
                <br><span class="description"><?php echo $text['description-max_turns']; ?></span>
            </td>
        </tr>

        <!-- Integration -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-integration']; ?></b></th>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-webhook_url']; ?></td>
            <td class="vtable">
                <input type="url" name="webhook_url" class="formfld" 
                    value="<?php echo escape($data['webhook_url'] ?? ''); ?>" placeholder="https://...">
                <br><span class="description"><?php echo $text['description-webhook_url']; ?></span>
            </td>
        </tr>
    </table>
</form>

<script>
function testVoice() {
    var text = "Olá! Este é um teste de voz da secretária virtual.";
    var voice = document.querySelector('input[name="tts_voice"]').value;
    
    // Make AJAX call to test voice
    fetch('secretary_test_voice.php', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: text, voice: voice})
    })
    .then(response => response.json())
    .then(data => {
        if (data.audio_url) {
            var audio = new Audio(data.audio_url);
            audio.play();
        } else {
            alert('<?php echo $text['message-voice_test_failed']; ?>');
        }
    })
    .catch(error => {
        alert('<?php echo $text['message-voice_test_failed']; ?>');
    });
}

// Toggle fields based on processing mode
function toggleModeFields() {
    var mode = document.querySelector('input[name="processing_mode"]:checked').value;
    var realtimeRows = document.querySelectorAll('#realtime_provider_row, #realtime_info_row');
    var turnBasedRows = document.querySelectorAll('[id^="turnbased_"]');
    
    if (mode === 'realtime' || mode === 'auto') {
        realtimeRows.forEach(function(row) { row.style.display = ''; });
    } else {
        realtimeRows.forEach(function(row) { row.style.display = 'none'; });
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    toggleModeFields();
});
</script>

<?php
// Include footer
require_once "resources/footer.php";
?>
