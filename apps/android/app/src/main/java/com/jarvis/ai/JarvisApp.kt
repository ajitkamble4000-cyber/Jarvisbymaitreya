package com.jarvis.ai

import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.TaskAlt
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class AppPage(val title: String) {
    HOME("JARVIS"), CHAT("Chat"), TASKS("Tasks"), MEMORY("Memory"), NOTIFICATIONS("Notifications"), SETTINGS("Settings")
}

private data class ChatLine(val user: Boolean, val text: String)

@Composable
fun JarvisApp() {
    val context = LocalContext.current
    val preferences = remember { context.getSharedPreferences("jarvis", 0) }
    val scope = rememberCoroutineScope()
    val gradient = Brush.verticalGradient(
        listOf(MaterialTheme.colorScheme.primary.copy(alpha = 0.16f), MaterialTheme.colorScheme.surface, MaterialTheme.colorScheme.background),
    )
    var page by remember { mutableStateOf(AppPage.HOME) }
    var baseUrl by remember { mutableStateOf(preferences.getString("base_url", "http://10.0.2.2:8000") ?: "http://10.0.2.2:8000") }
    var urlDraft by remember { mutableStateOf(baseUrl) }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var displayName by remember { mutableStateOf("") }
    var authenticated by remember { mutableStateOf(false) }
    var setupRequired by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    var conversationId by remember { mutableStateOf<String?>(null) }
    var draft by remember { mutableStateOf("") }
    var chatBusy by remember { mutableStateOf(false) }
    val chatLines = remember { mutableStateListOf<ChatLine>() }
    var records by remember { mutableStateOf<List<String>>(emptyList()) }

    LaunchedEffect(baseUrl) {
        loading = true
        error = null
        runCatching { withContext(Dispatchers.IO) { JarvisApi.authStatus(baseUrl) } }
            .onSuccess { status ->
                setupRequired = status.optBoolean("setup_required")
                authenticated = status.optBoolean("authenticated")
            }
            .onFailure { error = it.message ?: "Could not reach the JARVIS server." }
        loading = false
    }

    LaunchedEffect(page, authenticated) {
        val resource = when (page) {
            AppPage.TASKS -> "tasks"
            AppPage.MEMORY -> "memory"
            AppPage.NOTIFICATIONS -> "notifications"
            else -> null
        }
        if (resource != null && authenticated) {
            loading = true
            runCatching { withContext(Dispatchers.IO) { JarvisApi.listItems(baseUrl, resource) } }
                .onSuccess { records = it }
                .onFailure { error = it.message ?: "Could not load $resource." }
            loading = false
        }
    }

    fun authenticate() {
        if (email.isBlank() || password.isBlank()) {
            error = "Enter your account email and password."
            return
        }
        loading = true
        error = null
        scope.launch {
            runCatching {
                withContext(Dispatchers.IO) {
                    JarvisApi.signIn(baseUrl, email, password, setupRequired, displayName)
                }
            }.onSuccess {
                authenticated = true
                notice = "Connected to JARVIS."
                password = ""
            }.onFailure { error = it.message ?: "Sign-in failed." }
            loading = false
        }
    }

    fun sendMessage() {
        val text = draft.trim()
        if (text.isEmpty() || chatBusy) return
        if (!authenticated) {
            error = "Connect and sign in from Settings before starting a chat."
            page = AppPage.SETTINGS
            return
        }
        draft = ""
        chatLines.add(ChatLine(user = true, text = text))
        chatLines.add(ChatLine(user = false, text = ""))
        chatBusy = true
        error = null
        scope.launch {
            runCatching {
                val id = conversationId ?: withContext(Dispatchers.IO) { JarvisApi.createConversation(baseUrl) }
                conversationId = id
                withContext(Dispatchers.IO) {
                    JarvisApi.sendMessage(baseUrl, id, text) { fragment ->
                        if (fragment.isNotEmpty()) {
                            scope.launch {
                                val last = chatLines.lastIndex
                                if (last >= 0 && !chatLines[last].user) {
                                    chatLines[last] = chatLines[last].copy(text = chatLines[last].text + fragment)
                                }
                            }
                        }
                    }
                }
            }.onFailure {
                error = it.message ?: "The message could not be sent."
                if (chatLines.isNotEmpty() && !chatLines.last().user && chatLines.last().text.isBlank()) {
                    chatLines.removeAt(chatLines.lastIndex)
                }
            }
            chatBusy = false
        }
    }

    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().background(gradient).padding(horizontal = 18.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (page != AppPage.HOME) {
                    IconButton(onClick = { page = AppPage.HOME }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back to home")
                    }
                }
                Column(modifier = Modifier.weight(1f)) {
                    Text(if (page == AppPage.HOME) "JARVIS AI" else page.title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text(if (authenticated) "Connected" else "Not connected", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                }
                IconButton(onClick = { page = AppPage.NOTIFICATIONS }) {
                    Icon(Icons.Default.Notifications, contentDescription = "Notifications")
                }
                IconButton(onClick = { urlDraft = baseUrl; page = AppPage.SETTINGS }) {
                    Icon(Icons.Default.Settings, contentDescription = "Settings")
                }
            }

            when (page) {
                AppPage.HOME -> HomeContent(
                    modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                    authenticated = authenticated,
                    loading = loading,
                    onOpenChat = { page = AppPage.CHAT },
                    onOpenTasks = { page = AppPage.TASKS },
                    onOpenMemory = { page = AppPage.MEMORY },
                    onConnect = { page = AppPage.SETTINGS },
                )
                AppPage.CHAT -> ChatContent(
                    modifier = Modifier.weight(1f),
                    lines = chatLines,
                    draft = draft,
                    busy = chatBusy,
                    onDraftChange = { draft = it },
                    onSend = ::sendMessage,
                )
                AppPage.SETTINGS -> SettingsContent(
                    modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                    baseUrl = urlDraft,
                    email = email,
                    password = password,
                    displayName = displayName,
                    setupRequired = setupRequired,
                    loading = loading,
                    authenticated = authenticated,
                    onBaseUrlChange = { urlDraft = it },
                    onEmailChange = { email = it },
                    onPasswordChange = { password = it },
                    onDisplayNameChange = { displayName = it },
                    onSaveServer = {
                        baseUrl = urlDraft.trim().trimEnd('/')
                        preferences.edit().putString("base_url", baseUrl).apply()
                        authenticated = false
                        error = null
                    },
                    onSignIn = ::authenticate,
                    onSignOut = {
                        loading = true
                        scope.launch {
                            runCatching { withContext(Dispatchers.IO) { JarvisApi.logout(baseUrl) } }
                                .onSuccess {
                                    authenticated = false
                                    conversationId = null
                                    chatLines.clear()
                                    notice = "Signed out."
                                }
                                .onFailure { error = it.message ?: "Could not sign out." }
                            loading = false
                        }
                    },
                )
                AppPage.TASKS -> RecordsContent(
                    modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                    title = "Tasks", items = records, loading = loading, empty = "No tasks yet.",
                )
                AppPage.MEMORY -> RecordsContent(
                    modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                    title = "Memory", items = records, loading = loading, empty = "No saved memories yet.",
                )
                AppPage.NOTIFICATIONS -> RecordsContent(
                    modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                    title = "Notifications", items = records, loading = loading, empty = "No notifications yet.",
                )
            }

            error?.let { InlineMessage(it, isError = true) }
            notice?.let {
                InlineMessage(it, isError = false)
                TextButton(onClick = { notice = null }) { Text("Dismiss") }
            }

            Spacer(modifier = Modifier.weight(1f))
            BottomNavigation(page = page, onSelect = { page = it })
        }
    }

}

@Composable
private fun HomeContent(
    modifier: Modifier = Modifier,
    authenticated: Boolean,
    loading: Boolean,
    onOpenChat: () -> Unit,
    onOpenTasks: () -> Unit,
    onOpenMemory: () -> Unit,
    onConnect: () -> Unit,
) {
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Your assistant is ready.", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Text("Chat, review tasks, and manage saved memories from one place.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (!authenticated) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
                Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Connect your JARVIS account", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text("Set your server address and sign in to use live assistant features.", style = MaterialTheme.typography.bodyMedium)
                    Button(onClick = onConnect, modifier = Modifier.fillMaxWidth(), enabled = !loading) {
                        Text(if (loading) "Connecting…" else "Connect account")
                    }
                }
            }
        }
        ActionCard("Start a chat", "Send a message to your configured AI provider.", Icons.Default.Chat, onOpenChat)
        ActionCard("Tasks", "Review assistant tasks and approvals.", Icons.Default.TaskAlt, onOpenTasks)
        ActionCard("Memory", "Browse saved facts and notes.", Icons.Default.Memory, onOpenMemory)
    }
}

@Composable
private fun ActionCard(title: String, description: String, icon: androidx.compose.ui.graphics.vector.ImageVector, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Column {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun SettingsContent(
    modifier: Modifier = Modifier,
    baseUrl: String,
    email: String,
    password: String,
    displayName: String,
    setupRequired: Boolean,
    loading: Boolean,
    authenticated: Boolean,
    onBaseUrlChange: (String) -> Unit,
    onEmailChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onDisplayNameChange: (String) -> Unit,
    onSaveServer: () -> Unit,
    onSignIn: () -> Unit,
    onSignOut: () -> Unit,
) {
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Connection", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        OutlinedTextField(baseUrl, onBaseUrlChange, modifier = Modifier.fillMaxWidth(), label = { Text("Backend URL") }, placeholder = { Text("https://your-server") }, singleLine = true)
        Text("For the Android emulator use http://10.0.2.2:8000. On a phone, use your server’s reachable URL.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = onSaveServer, modifier = Modifier.fillMaxWidth()) { Text("Save and connect") }
        if (authenticated) {
            Text("Signed in and ready.", color = MaterialTheme.colorScheme.primary)
            OutlinedButton(onClick = onSignOut, modifier = Modifier.fillMaxWidth()) { Text("Sign out") }
        } else {
            Spacer(Modifier.height(8.dp))
            Text(if (setupRequired) "Create owner account" else "Sign in", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            if (setupRequired) OutlinedTextField(displayName, onDisplayNameChange, modifier = Modifier.fillMaxWidth(), label = { Text("Display name") }, singleLine = true)
            OutlinedTextField(email, onEmailChange, modifier = Modifier.fillMaxWidth(), label = { Text("Email") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), singleLine = true)
            OutlinedTextField(password, onPasswordChange, modifier = Modifier.fillMaxWidth(), label = { Text("Password") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
            Button(onClick = onSignIn, modifier = Modifier.fillMaxWidth(), enabled = !loading) {
                if (loading) CircularProgressIndicator(modifier = Modifier.height(18.dp), strokeWidth = 2.dp) else Text(if (setupRequired) "Create account" else "Sign in")
            }
            if (setupRequired) Text("Use a password with at least 12 characters.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ChatContent(
    modifier: Modifier = Modifier,
    lines: List<ChatLine>,
    draft: String,
    busy: Boolean,
    onDraftChange: (String) -> Unit,
    onSend: () -> Unit,
) {
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        LazyColumn(modifier = Modifier.weight(1f).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (lines.isEmpty()) item { Text("Ask JARVIS anything. Your message will use the provider configured on your server.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(lines) { line ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                    color = if (line.user) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                ) {
                    Text(line.text.ifBlank { if (busy && !line.user) "Thinking…" else "" }, Modifier.padding(12.dp))
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = draft,
                onValueChange = onDraftChange,
                modifier = Modifier.weight(1f),
                label = { Text("Message") },
                enabled = !busy,
                maxLines = 4,
            )
            IconButton(onClick = onSend, enabled = !busy && draft.isNotBlank()) {
                if (busy) CircularProgressIndicator(modifier = Modifier.height(20.dp), strokeWidth = 2.dp)
                else Icon(Icons.Default.Send, contentDescription = "Send message")
            }
        }
    }
}

@Composable
private fun RecordsContent(modifier: Modifier = Modifier, title: String, items: List<String>, loading: Boolean, empty: String) {
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        when {
            loading -> CircularProgressIndicator()
            items.isEmpty() -> Text(empty, color = MaterialTheme.colorScheme.onSurfaceVariant)
            else -> items.forEach { item ->
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
                    Text(item, Modifier.fillMaxWidth().padding(14.dp))
                }
            }
        }
    }
}

@Composable
private fun BottomNavigation(page: AppPage, onSelect: (AppPage) -> Unit) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.CenterVertically) {
        NavigationItem("Home", Icons.Default.Home, page == AppPage.HOME) { onSelect(AppPage.HOME) }
        NavigationItem("Chat", Icons.Default.Chat, page == AppPage.CHAT) { onSelect(AppPage.CHAT) }
        NavigationItem("Tasks", Icons.Default.TaskAlt, page == AppPage.TASKS) { onSelect(AppPage.TASKS) }
        NavigationItem("Memory", Icons.Default.Memory, page == AppPage.MEMORY) { onSelect(AppPage.MEMORY) }
        NavigationItem("Settings", Icons.Default.Settings, page == AppPage.SETTINGS) { onSelect(AppPage.SETTINGS) }
    }
}

@Composable
private fun NavigationItem(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector, selected: Boolean, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        IconButton(onClick = onClick) {
            Icon(icon, contentDescription = label, tint = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(label, style = MaterialTheme.typography.labelSmall, color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun InlineMessage(message: String, isError: Boolean) {
    Text(
        message,
        modifier = Modifier.fillMaxWidth().padding(8.dp),
        color = if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
        style = MaterialTheme.typography.bodySmall,
    )
}