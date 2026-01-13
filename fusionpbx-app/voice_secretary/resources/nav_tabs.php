<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Navigation Tabs
	Include this after require_once "resources/header.php";
	Set $current_page before including.
*/

//check if $current_page is set
	if (!isset($current_page)) {
		$current_page = 'secretaries';
	}

//define navigation items
	$nav_items = [
		'secretaries' => [
			'title' => $text['label-secretaries'] ?? 'Secretaries',
			'path' => 'secretary.php',
			'permission' => 'voice_secretary_view'
		],
		'providers' => [
			'title' => $text['label-ai_providers'] ?? 'AI Providers',
			'path' => 'providers.php',
			'permission' => 'voice_secretary_providers'
		],
		'documents' => [
			'title' => $text['label-documents'] ?? 'Documents',
			'path' => 'documents.php',
			'permission' => 'voice_secretary_documents'
		],
		'conversations' => [
			'title' => $text['label-conversations'] ?? 'Conversations',
			'path' => 'conversations.php',
			'permission' => 'voice_secretary_view'
		],
		'settings' => [
			'title' => $text['label-settings'] ?? 'Settings',
			'path' => 'settings.php',
			'permission' => 'voice_secretary_edit'
		],
	];

//count visible tabs
	$visible_tabs = 0;
	foreach ($nav_items as $item) {
		if (permission_exists($item['permission'])) {
			$visible_tabs++;
		}
	}

//only show tabs if more than one is visible
	if ($visible_tabs > 1) {
		echo "<div class='tab_bar'>\n";
		echo "	<div class='tab_bar_inner'>\n";
		foreach ($nav_items as $key => $item) {
			if (permission_exists($item['permission'])) {
				$active = ($current_page === $key) ? ' active' : '';
				echo "		<a class='tab".$active."' href='".$item['path']."'>".$item['title']."</a>\n";
			}
		}
		echo "	</div>\n";
		echo "</div>\n";
		echo "<br />\n";
	}

?>
