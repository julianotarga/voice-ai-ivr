<?php
/**
 * Voice Secretary - Realtime Provider Edit
 * 
 * Configure realtime AI providers (OpenAI, ElevenLabs, Gemini, Custom).
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

//initialize
$provider_obj = new voice_ai_provider();
$action = 'add';
$data = [];

// Check if editing existing
if (isset($_GET['id']) && !empty($_GET['id'])) {
    $action = 'edit';
    $provider_uuid = $_GET['id'];
    $data = $provider_obj->get($provider_uuid);
    
    if (!$data) {
        $_SESSION['message'] = $text['message-provider_not_found'] ?? 'Provider not found';
        header('Location: providers.php');
        exit;
    }
}

// Realtime provider options
$realtime_providers = [
    'openai' => [
        'name' => 'OpenAI Realtime API',
        'description' => 'GPT-4o Realtime with voice',
        'fields' => ['api_key', 'model', 'voice'],
        'voices' => ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'],
        'models' => ['gpt-4o-realtime-preview'],
    ],
    'elevenlabs' => [
        'name' => 'ElevenLabs Conversational AI',
        'description' => 'Premium voices with conversational AI',
        'fields' => ['api_key', 'agent_id', 'voice_id'],
    ],
    'gemini' => [
        'name' => 'Google Gemini 2.0 Flash',
        'description' => 'Multimodal AI with audio',
        'fields' => ['api_key', 'model', 'voice'],
        'voices' => ['Aoede', 'Charon', 'Fenrir', 'Kore', 'Puck'],
        'models' => ['gemini-2.0-flash-exp'],
    ],
    'custom' => [
        'name' => 'Custom Pipeline',
        'description' => 'Deepgram + Groq + Piper (low-cost)',
        'fields' => ['deepgram_key', 'groq_key', 'stt_provider', 'llm_provider', 'tts_provider'],
    ],
];

// Process form submission
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['submit'])) {
    $provider_name = $_POST['provider_name'] ?? '';
    
    // Build config based on provider
    $config = [];
    if (isset($realtime_providers[$provider_name])) {
        foreach ($realtime_providers[$provider_name]['fields'] as $field) {
            if (isset($_POST[$field])) {
                $config[$field] = $_POST[$field];
            }
        }
    }
    
    $form_data = [
        'provider_type' => 'realtime',
        'provider_name' => $provider_name,
        'config' => json_encode($config),
        'is_default' => isset($_POST['is_default']),
        'is_enabled' => isset($_POST['is_enabled']),
    ];
    
    // Validate
    if (empty($provider_name)) {
        $_SESSION['message'] = $text['message-provider_required'] ?? 'Provider is required';
    } else {
        try {
            if ($action === 'add') {
                $provider_obj->create($form_data);
                $_SESSION['message'] = $text['message-provider_created'] ?? 'Provider created';
            } else {
                $provider_obj->update($provider_uuid, $form_data);
                $_SESSION['message'] = $text['message-provider_updated'] ?? 'Provider updated';
            }
            header('Location: providers.php');
            exit;
        } catch (Exception $e) {
            $_SESSION['message'] = ($text['message-error'] ?? 'Error') . ': ' . $e->getMessage();
        }
    }
}

// Parse existing config
$config = [];
if (!empty($data['config'])) {
    $config = json_decode($data['config'], true) ?: [];
}

// Include header
$document['title'] = ($action === 'add') 
    ? ($text['title-add_realtime_provider'] ?? 'Add Realtime Provider')
    : ($text['title-edit_realtime_provider'] ?? 'Edit Realtime Provider');
require_once "resources/header.php";
?>

<form method="post">
    <div class="action_bar" id="action_bar">
        <div class="heading">
            <b><?php echo $document['title']; ?></b>
        </div>
        <div class="actions">
            <button type="submit" name="submit" class="btn btn-primary btn-sm">
                <span class="fas fa-save fa-fw"></span>
                <?php echo $text['button-save'] ?? 'Save'; ?>
            </button>
            <button type="button" onclick="window.location='providers.php'" class="btn btn-default btn-sm">
                <span class="fas fa-times fa-fw"></span>
                <?php echo $text['button-back'] ?? 'Back'; ?>
            </button>
        </div>
        <div style="clear: both;"></div>
    </div>

    <table class="form_table">
        <!-- Provider Selection -->
        <tr>
            <th colspan="2"><b><?php echo $text['header-realtime_provider'] ?? 'Realtime Provider'; ?></b></th>
        </tr>
        <tr>
            <td class="vncellreq"><?php echo $text['label-provider'] ?? 'Provider'; ?></td>
            <td class="vtable">
                <select name="provider_name" class="formfld" onchange="showProviderFields(this.value)" required>
                    <option value=""><?php echo $text['option-select'] ?? 'Select...'; ?></option>
                    <?php foreach ($realtime_providers as $key => $provider) { ?>
                        <option value="<?php echo $key; ?>" 
                            <?php echo (($data['provider_name'] ?? '') === $key) ? 'selected' : ''; ?>>
                            <?php echo escape($provider['name']); ?> - <?php echo escape($provider['description']); ?>
                        </option>
                    <?php } ?>
                </select>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-enabled'] ?? 'Enabled'; ?></td>
            <td class="vtable">
                <input type="checkbox" name="is_enabled" <?php echo (!isset($data['is_enabled']) || $data['is_enabled']) ? 'checked' : ''; ?>>
            </td>
        </tr>
        <tr>
            <td class="vncell"><?php echo $text['label-default'] ?? 'Default'; ?></td>
            <td class="vtable">
                <input type="checkbox" name="is_default" <?php echo (($data['is_default'] ?? false)) ? 'checked' : ''; ?>>
                <br><span class="description"><?php echo $text['description-default'] ?? 'Set as default realtime provider'; ?></span>
            </td>
        </tr>

        <!-- OpenAI Fields -->
        <tbody id="fields_openai" class="provider_fields" style="display: none;">
            <tr>
                <th colspan="2"><b>OpenAI Configuration</b></th>
            </tr>
            <tr>
                <td class="vncellreq">API Key</td>
                <td class="vtable">
                    <input type="password" name="api_key" class="formfld" 
                        value="<?php echo escape($config['api_key'] ?? ''); ?>" placeholder="sk-...">
                </td>
            </tr>
            <tr>
                <td class="vncell">Model</td>
                <td class="vtable">
                    <select name="model" class="formfld">
                        <option value="gpt-4o-realtime-preview" <?php echo (($config['model'] ?? '') === 'gpt-4o-realtime-preview') ? 'selected' : ''; ?>>gpt-4o-realtime-preview</option>
                    </select>
                </td>
            </tr>
            <tr>
                <td class="vncell">Voice</td>
                <td class="vtable">
                    <select name="voice" class="formfld">
                        <?php foreach (['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] as $v) { ?>
                            <option value="<?php echo $v; ?>" <?php echo (($config['voice'] ?? '') === $v) ? 'selected' : ''; ?>><?php echo ucfirst($v); ?></option>
                        <?php } ?>
                    </select>
                </td>
            </tr>
        </tbody>

        <!-- ElevenLabs Fields -->
        <tbody id="fields_elevenlabs" class="provider_fields" style="display: none;">
            <tr>
                <th colspan="2"><b>ElevenLabs Configuration</b></th>
            </tr>
            <tr>
                <td class="vncellreq">API Key</td>
                <td class="vtable">
                    <input type="password" name="api_key" class="formfld" 
                        value="<?php echo escape($config['api_key'] ?? ''); ?>">
                </td>
            </tr>
            <tr>
                <td class="vncellreq">Agent ID</td>
                <td class="vtable">
                    <input type="text" name="agent_id" class="formfld" 
                        value="<?php echo escape($config['agent_id'] ?? ''); ?>" placeholder="agent_...">
                    <br><span class="description">Create an agent at <a href="https://elevenlabs.io/app/conversational-ai" target="_blank">ElevenLabs Console</a></span>
                </td>
            </tr>
            <tr>
                <td class="vncell">Voice ID</td>
                <td class="vtable">
                    <input type="text" name="voice_id" class="formfld" 
                        value="<?php echo escape($config['voice_id'] ?? ''); ?>" placeholder="Optional - uses agent default">
                </td>
            </tr>
        </tbody>

        <!-- Gemini Fields -->
        <tbody id="fields_gemini" class="provider_fields" style="display: none;">
            <tr>
                <th colspan="2"><b>Google Gemini Configuration</b></th>
            </tr>
            <tr>
                <td class="vncellreq">API Key</td>
                <td class="vtable">
                    <input type="password" name="api_key" class="formfld" 
                        value="<?php echo escape($config['api_key'] ?? ''); ?>">
                    <br><span class="description">Get key from <a href="https://aistudio.google.com/apikey" target="_blank">Google AI Studio</a></span>
                </td>
            </tr>
            <tr>
                <td class="vncell">Model</td>
                <td class="vtable">
                    <select name="model" class="formfld">
                        <option value="gemini-2.0-flash-exp">gemini-2.0-flash-exp</option>
                    </select>
                </td>
            </tr>
            <tr>
                <td class="vncell">Voice</td>
                <td class="vtable">
                    <select name="voice" class="formfld">
                        <?php foreach (['Aoede', 'Charon', 'Fenrir', 'Kore', 'Puck'] as $v) { ?>
                            <option value="<?php echo $v; ?>" <?php echo (($config['voice'] ?? '') === $v) ? 'selected' : ''; ?>><?php echo $v; ?></option>
                        <?php } ?>
                    </select>
                </td>
            </tr>
        </tbody>

        <!-- Custom Pipeline Fields -->
        <tbody id="fields_custom" class="provider_fields" style="display: none;">
            <tr>
                <th colspan="2"><b>Custom Pipeline Configuration</b></th>
            </tr>
            <tr>
                <td class="vncell">STT Provider</td>
                <td class="vtable">
                    <select name="stt_provider" class="formfld">
                        <option value="deepgram" <?php echo (($config['stt_provider'] ?? '') === 'deepgram') ? 'selected' : ''; ?>>Deepgram Nova</option>
                        <option value="whisper" <?php echo (($config['stt_provider'] ?? '') === 'whisper') ? 'selected' : ''; ?>>Whisper Local</option>
                    </select>
                </td>
            </tr>
            <tr>
                <td class="vncell">Deepgram API Key</td>
                <td class="vtable">
                    <input type="password" name="deepgram_key" class="formfld" 
                        value="<?php echo escape($config['deepgram_key'] ?? ''); ?>">
                </td>
            </tr>
            <tr>
                <td class="vncell">LLM Provider</td>
                <td class="vtable">
                    <select name="llm_provider" class="formfld">
                        <option value="groq" <?php echo (($config['llm_provider'] ?? '') === 'groq') ? 'selected' : ''; ?>>Groq (Llama)</option>
                        <option value="ollama" <?php echo (($config['llm_provider'] ?? '') === 'ollama') ? 'selected' : ''; ?>>Ollama Local</option>
                    </select>
                </td>
            </tr>
            <tr>
                <td class="vncell">Groq API Key</td>
                <td class="vtable">
                    <input type="password" name="groq_key" class="formfld" 
                        value="<?php echo escape($config['groq_key'] ?? ''); ?>">
                </td>
            </tr>
            <tr>
                <td class="vncell">TTS Provider</td>
                <td class="vtable">
                    <select name="tts_provider" class="formfld">
                        <option value="piper" <?php echo (($config['tts_provider'] ?? '') === 'piper') ? 'selected' : ''; ?>>Piper Local</option>
                        <option value="coqui" <?php echo (($config['tts_provider'] ?? '') === 'coqui') ? 'selected' : ''; ?>>Coqui Local</option>
                    </select>
                </td>
            </tr>
        </tbody>
    </table>
</form>

<script>
function showProviderFields(provider) {
    // Hide all provider fields
    document.querySelectorAll('.provider_fields').forEach(function(el) {
        el.style.display = 'none';
    });
    
    // Show selected provider fields
    if (provider) {
        var fields = document.getElementById('fields_' + provider);
        if (fields) {
            fields.style.display = '';
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    var selected = document.querySelector('select[name="provider_name"]').value;
    if (selected) {
        showProviderFields(selected);
    }
});
</script>

<?php
require_once "resources/footer.php";
?>
