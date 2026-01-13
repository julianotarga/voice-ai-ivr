<?php
/**
 * Voice Secretary - Provider Edit Page
 * 
 * Create or edit an AI provider.
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

//include classes
	require_once "resources/classes/voice_ai_provider.php";

$provider_obj = new voice_ai_provider();
$action = 'add';
$data = [];

// Check if editing existing
if (isset($_GET['id']) && !empty($_GET['id'])) {
    $action = 'edit';
    $provider_uuid = $_GET['id'];
    $data = $provider_obj->get($provider_uuid);
    
    if (!$data) {
        $_SESSION['message'] = $text['message-provider_not_found'];
        header('Location: providers.php');
        exit;
    }
    
    // Decode config
    $data['config'] = json_decode($data['config'], true) ?: [];
}

// Process form submission
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {
    // Build config from form
    $config = [];
    if (isset($_POST['config']) && is_array($_POST['config'])) {
        $config = $_POST['config'];
    }
    
    $form_data = [
        'provider_type' => $_POST['provider_type'] ?? '',
        'provider_name' => $_POST['provider_name'] ?? '',
        'config' => $config,
        'is_active' => isset($_POST['is_active']),
        'is_default' => isset($_POST['is_default']),
        'priority' => intval($_POST['priority'] ?? 10),
    ];
    
    try {
        if ($action === 'add') {
            $provider_obj->create($form_data);
            $_SESSION['message'] = $text['message-provider_created'];
        } else {
            $provider_obj->update($provider_uuid, $form_data);
            $_SESSION['message'] = $text['message-provider_updated'];
        }
        header('Location: providers.php');
        exit;
    } catch (Exception $e) {
        $_SESSION['message'] = $text['message-error'] . ': ' . $e->getMessage();
    }
}

// Get provider options
$all_providers = voice_ai_provider::PROVIDERS;

// Include header
$document['title'] = ($action === 'add') ? $text['title-add_provider'] : $text['title-edit_provider'];
require_once "resources/header.php";
?>

<form method="post" id="provider_form">
    <div class="action_bar" id="action_bar">
        <div class="heading">
            <b><?php echo ($action === 'add') ? $text['title-add_provider'] : $text['title-edit_provider']; ?></b>
        </div>
        <div class="actions">
            <button type="submit" name="submit" class="btn btn-primary btn-sm">
                <span class="fas fa-save fa-fw"></span>
                <?php echo $text['button-save']; ?>
            </button>
            <button type="button" onclick="window.location='providers.php'" class="btn btn-default btn-sm">
                <span class="fas fa-times fa-fw"></span>
                <?php echo $text['button-back']; ?>
            </button>
        </div>
        <div style="clear: both;"></div>
    </div>

    <table class="form_table">
        <tr>
            <td class="vncellreq"><?php echo $text['label-provider_type']; ?></td>
            <td class="vtable">
                <select name="provider_type" id="provider_type" class="formfld" required 
                        onchange="updateProviderOptions()" <?php echo ($action === 'edit') ? 'disabled' : ''; ?>>
                    <option value=""><?php echo $text['option-select']; ?></option>
                    <option value="stt" <?php echo (($data['provider_type'] ?? '') === 'stt') ? 'selected' : ''; ?>>Speech-to-Text (STT)</option>
                    <option value="tts" <?php echo (($data['provider_type'] ?? '') === 'tts') ? 'selected' : ''; ?>>Text-to-Speech (TTS)</option>
                    <option value="llm" <?php echo (($data['provider_type'] ?? '') === 'llm') ? 'selected' : ''; ?>>Large Language Model (LLM)</option>
                    <option value="embeddings" <?php echo (($data['provider_type'] ?? '') === 'embeddings') ? 'selected' : ''; ?>>Embeddings</option>
                </select>
                <?php if ($action === 'edit') { ?>
                    <input type="hidden" name="provider_type" value="<?php echo escape($data['provider_type']); ?>">
                <?php } ?>
            </td>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-provider_name']; ?></td>
            <td class="vtable">
                <select name="provider_name" id="provider_name" class="formfld" required 
                        onchange="updateConfigFields()" <?php echo ($action === 'edit') ? 'disabled' : ''; ?>>
                    <option value=""><?php echo $text['option-select']; ?></option>
                    <?php if ($action === 'edit' && isset($data['provider_type'])) { 
                        foreach ($all_providers[$data['provider_type']] ?? [] as $key => $label) { ?>
                            <option value="<?php echo $key; ?>" <?php echo (($data['provider_name'] ?? '') === $key) ? 'selected' : ''; ?>>
                                <?php echo escape($label); ?>
                            </option>
                        <?php } 
                    } ?>
                </select>
                <?php if ($action === 'edit') { ?>
                    <input type="hidden" name="provider_name" value="<?php echo escape($data['provider_name']); ?>">
                <?php } ?>
            </td>
        </tr>
        
        <!-- Dynamic config fields -->
        <tbody id="config_fields">
            <?php if ($action === 'edit' && !empty($data['provider_name'])) { 
                $fields = voice_ai_provider::get_config_fields($data['provider_name']);
                foreach ($fields as $field) {
                    $value = $data['config'][$field['name']] ?? ($field['default'] ?? '');
            ?>
                <tr>
                    <td class="vncell<?php echo !empty($field['required']) ? 'req' : ''; ?>">
                        <?php echo escape($field['label']); ?>
                    </td>
                    <td class="vtable">
                        <?php if ($field['type'] === 'select' && isset($field['options'])) { ?>
                            <select name="config[<?php echo escape($field['name']); ?>]" class="formfld">
                                <?php foreach ($field['options'] as $opt) { ?>
                                    <option value="<?php echo escape($opt); ?>" <?php echo ($value === $opt) ? 'selected' : ''; ?>>
                                        <?php echo escape($opt); ?>
                                    </option>
                                <?php } ?>
                            </select>
                        <?php } elseif ($field['type'] === 'password') { ?>
                            <input type="password" name="config[<?php echo escape($field['name']); ?>]" 
                                   class="formfld" value="<?php echo escape($value); ?>"
                                   <?php echo !empty($field['required']) ? 'required' : ''; ?>>
                        <?php } else { ?>
                            <input type="text" name="config[<?php echo escape($field['name']); ?>]" 
                                   class="formfld" value="<?php echo escape($value); ?>"
                                   <?php echo !empty($field['required']) ? 'required' : ''; ?>>
                        <?php } ?>
                    </td>
                </tr>
            <?php } 
            } ?>
        </tbody>
        
        <tr>
            <td class="vncell"><?php echo $text['label-priority']; ?></td>
            <td class="vtable">
                <input type="number" name="priority" class="formfld" min="1" max="100"
                    value="<?php echo intval($data['priority'] ?? 10); ?>">
                <br><span class="description"><?php echo $text['description-priority']; ?></span>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-default']; ?></td>
            <td class="vtable">
                <input type="checkbox" name="is_default" <?php echo !empty($data['is_default']) ? 'checked' : ''; ?>>
                <?php echo $text['description-default']; ?>
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

<script>
const providers = <?php echo json_encode($all_providers); ?>;
const configFields = <?php echo json_encode([
    'whisper_local' => voice_ai_provider::get_config_fields('whisper_local'),
    'whisper_api' => voice_ai_provider::get_config_fields('whisper_api'),
    'azure_speech' => voice_ai_provider::get_config_fields('azure_speech'),
    'google_speech' => voice_ai_provider::get_config_fields('google_speech'),
    'deepgram' => voice_ai_provider::get_config_fields('deepgram'),
    'openai_tts' => voice_ai_provider::get_config_fields('openai_tts'),
    'elevenlabs' => voice_ai_provider::get_config_fields('elevenlabs'),
    'openai' => voice_ai_provider::get_config_fields('openai'),
    'anthropic' => voice_ai_provider::get_config_fields('anthropic'),
    'ollama_local' => voice_ai_provider::get_config_fields('ollama_local'),
]); ?>;

function updateProviderOptions() {
    const type = document.getElementById('provider_type').value;
    const select = document.getElementById('provider_name');
    
    select.innerHTML = '<option value=""><?php echo $text['option-select']; ?></option>';
    
    if (type && providers[type]) {
        for (const [key, label] of Object.entries(providers[type])) {
            const option = document.createElement('option');
            option.value = key;
            option.textContent = label;
            select.appendChild(option);
        }
    }
    
    // Clear config fields
    document.getElementById('config_fields').innerHTML = '';
}

function updateConfigFields() {
    const providerName = document.getElementById('provider_name').value;
    const container = document.getElementById('config_fields');
    
    container.innerHTML = '';
    
    if (providerName && configFields[providerName]) {
        configFields[providerName].forEach(field => {
            const tr = document.createElement('tr');
            const tdLabel = document.createElement('td');
            const tdInput = document.createElement('td');
            
            tdLabel.className = 'vncell' + (field.required ? 'req' : '');
            tdLabel.textContent = field.label;
            
            tdInput.className = 'vtable';
            
            let input;
            if (field.type === 'select' && field.options) {
                input = document.createElement('select');
                input.className = 'formfld';
                input.name = 'config[' + field.name + ']';
                field.options.forEach(opt => {
                    const option = document.createElement('option');
                    option.value = opt;
                    option.textContent = opt;
                    if (opt === field.default) option.selected = true;
                    input.appendChild(option);
                });
            } else {
                input = document.createElement('input');
                input.type = field.type === 'password' ? 'password' : 'text';
                input.className = 'formfld';
                input.name = 'config[' + field.name + ']';
                if (field.default) input.value = field.default;
                if (field.required) input.required = true;
            }
            
            tdInput.appendChild(input);
            tr.appendChild(tdLabel);
            tr.appendChild(tdInput);
            container.appendChild(tr);
        });
    }
}
</script>

<?php
// Include footer
require_once "resources/footer.php";
?>
