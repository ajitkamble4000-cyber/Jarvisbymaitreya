package com.jarvis.ai

import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.CookieHandler
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL

internal fun normalizeBaseUrl(value: String): String {
    val normalized = value.trim().trimEnd('/')
    val uri = runCatching { URI(normalized) }
        .getOrElse { throw IllegalArgumentException("Enter a valid backend URL.") }
    require(uri.scheme == "http" || uri.scheme == "https") {
        "Backend URL must begin with http:// or https://."
    }
    require(!uri.host.isNullOrBlank() && uri.userInfo == null && uri.query == null && uri.fragment == null) {
        "Enter a backend URL with a host and no credentials, query, or fragment."
    }
    return normalized
}

object JarvisApi {
    private val cookies = CookieManager(null, CookiePolicy.ACCEPT_ALL).also {
        CookieHandler.setDefault(it)
    }

    fun authStatus(baseUrl: String): JSONObject = request(baseUrl, "GET", "/auth/status")

    fun logout(baseUrl: String) {
        request(baseUrl, "POST", "/auth/logout", JSONObject())
        cookies.cookieStore.removeAll()
    }

    fun signIn(baseUrl: String, email: String, password: String, setup: Boolean, displayName: String): JSONObject {
        val body = JSONObject()
            .put("email", email.trim())
            .put("password", password)
        val path = if (setup) {
            body.put("display_name", displayName.trim())
            "/auth/setup"
        } else {
            "/auth/login"
        }
        return request(baseUrl, "POST", path, body)
    }

    fun createConversation(baseUrl: String): String {
        val result = request(baseUrl, "POST", "/conversations", JSONObject())
        return result.getString("id")
    }

    fun sendMessage(baseUrl: String, conversationId: String, content: String, onText: (String) -> Unit) {
        val connection = openConnection(baseUrl, "POST", "/conversations/$conversationId/messages")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        connection.outputStream.bufferedWriter().use {
            it.write(JSONObject().put("content", content).toString())
        }
        val status = connection.responseCode
        if (status !in 200..299) {
            throw IllegalStateException(readError(connection))
        }
        var event = ""
        val data = StringBuilder()
        try {
            BufferedReader(InputStreamReader(connection.inputStream)).use { reader ->
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith("event:") -> event = line.substringAfter(':').trim()
                        line.startsWith("data:") -> data.append(line.substringAfter(':').trim())
                        line.isEmpty() -> {
                            if (data.isNotEmpty()) {
                                val payload = JSONObject(data.toString())
                                when (event) {
                                    "text_delta" -> onText(payload.optString("text"))
                                    "error" -> throw IllegalStateException(payload.optString("message", "The assistant reply failed."))
                                }
                            }
                            event = ""
                            data.setLength(0)
                        }
                    }
                }
            }
        } finally {
            connection.disconnect()
        }
    }

    fun listItems(baseUrl: String, resource: String): List<String> {
        val response = request(baseUrl, "GET", "/$resource?limit=20")
        val values = response.optJSONArray("items") ?: return emptyList()
        return (0 until values.length()).mapNotNull { index ->
            val item = values.optJSONObject(index) ?: return@mapNotNull null
            item.optString("title")
                .ifBlank { item.optString("content") }
                .ifBlank { item.optString("description") }
                .ifBlank { item.optString("body") }
        }
    }

    fun listTasks(baseUrl: String): List<TaskItem> {
        val response = request(baseUrl, "GET", "/tasks?limit=20")
        val values = response.optJSONArray("items") ?: return emptyList()
        return (0 until values.length()).mapNotNull { index ->
            values.optJSONObject(index)?.let(::taskItemFromJson)
        }
    }

    fun runTaskAction(baseUrl: String, taskId: String, action: TaskAction) {
        request(baseUrl, "POST", "/tasks/$taskId/${action.path}")
    }

    private fun request(baseUrl: String, method: String, path: String, body: JSONObject? = null): JSONObject {
        val connection = openConnection(baseUrl, method, path)
        if (body != null) {
            connection.setRequestProperty("Content-Type", "application/json")
            connection.doOutput = true
            connection.outputStream.bufferedWriter().use { it.write(body.toString()) }
        }
        val status = connection.responseCode
        val text = (if (status in 200..299) connection.inputStream else connection.errorStream)
            ?.bufferedReader()?.use { it.readText() }.orEmpty()
        connection.disconnect()
        if (status !in 200..299) {
            val message = runCatching { JSONObject(text).getJSONObject("error").getString("message") }.getOrNull()
            throw IllegalStateException(message ?: "JARVIS server returned HTTP $status")
        }
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }

    private fun openConnection(baseUrl: String, method: String, path: String): HttpURLConnection {
        val root = normalizeBaseUrl(baseUrl)
        val connection = URL("$root/api/v1$path").openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 12_000
        connection.readTimeout = if (path.endsWith("/messages")) 120_000 else 20_000
        connection.setRequestProperty("Accept", if (path.endsWith("/messages")) "text/event-stream" else "application/json")
        connection.setRequestProperty("X-Request-ID", java.util.UUID.randomUUID().toString())
        val csrf = cookies.cookieStore.cookies.firstOrNull { it.name == "jarvis_csrf" }?.value
        if (csrf != null) connection.setRequestProperty("X-CSRF-Token", csrf)
        return connection
    }

    private fun readError(connection: HttpURLConnection): String {
        val text = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
        connection.disconnect()
        return runCatching { JSONObject(text).getJSONObject("error").getString("message") }
            .getOrElse { "JARVIS server returned HTTP ${connection.responseCode}" }
    }
}

enum class TaskAction(val path: String) {
    APPROVE("approve"), DENY("deny"), CANCEL("cancel"), RETRY("retry")
}

data class TaskItem(
    val id: String,
    val title: String,
    val status: String,
    val detail: String?,
    val error: String?,
    val actions: Set<TaskAction>,
)

internal fun taskItemFromJson(json: JSONObject): TaskItem {
    val actionJson = json.optJSONObject("actions") ?: JSONObject()
    val result = json.optJSONObject("result")
    val errorMessage = json.optJSONObject("error")?.optString("message")?.takeIf(String::isNotBlank)
    val detail = json.optString("current_step").takeIf(String::isNotBlank)
        ?: result?.optString("message")?.takeIf(String::isNotBlank)
    return taskItemFromFields(
        id = json.optString("id"),
        title = json.optString("title"),
        status = json.optString("status"),
        detail = detail,
        error = errorMessage,
        canApprove = actionJson.optBoolean("approve"),
        canDeny = actionJson.optBoolean("deny"),
        canCancel = actionJson.optBoolean("cancel"),
        canRetry = actionJson.optBoolean("retry"),
    )
}

internal fun taskItemFromFields(
    id: String,
    title: String,
    status: String,
    detail: String?,
    error: String?,
    canApprove: Boolean,
    canDeny: Boolean,
    canCancel: Boolean,
    canRetry: Boolean,
): TaskItem {
    val available = buildSet {
        if (canApprove) add(TaskAction.APPROVE)
        if (canDeny) add(TaskAction.DENY)
        if (canCancel) add(TaskAction.CANCEL)
        if (canRetry) add(TaskAction.RETRY)
    }
    return TaskItem(
        id = id,
        title = title.ifBlank { "Untitled task" },
        status = status.ifBlank { "unknown" },
        detail = detail,
        error = error,
        actions = available,
    )
}