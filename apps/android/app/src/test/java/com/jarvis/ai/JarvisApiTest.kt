package com.jarvis.ai

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class JarvisApiTest {
    @Test
    fun normalizesTrailingSlashesAndWhitespace() {
        assertEquals("https://jarvis.example", normalizeBaseUrl("  https://jarvis.example///  "))
    }

    @Test
    fun acceptsEmulatorLoopbackHttpForDevelopment() {
        assertEquals("http://10.0.2.2:8000", normalizeBaseUrl("http://10.0.2.2:8000/"))
    }

    @Test
    fun rejectsUnsupportedSchemes() {
        assertThrows(IllegalArgumentException::class.java) {
            normalizeBaseUrl("file:///tmp/jarvis")
        }
    }

    @Test
    fun rejectsUrlsContainingCredentialsOrQueryData() {
        assertThrows(IllegalArgumentException::class.java) {
            normalizeBaseUrl("https://user:password@jarvis.example")
        }
        assertThrows(IllegalArgumentException::class.java) {
            normalizeBaseUrl("https://jarvis.example?token=secret")
        }
    }

    @Test
    fun parsesBackendTaskStatusAndOnlyAvailableActions() {
        val task = taskItemFromFields(
            id = "task-1",
            title = "Open project",
            status = "awaiting_approval",
            detail = "Waiting for approval",
            error = null,
            canApprove = true,
            canDeny = true,
            canCancel = false,
            canRetry = false,
        )

        assertEquals("Open project", task.title)
        assertEquals("awaiting_approval", task.status)
        assertEquals("Waiting for approval", task.detail)
        assertEquals(setOf(TaskAction.APPROVE, TaskAction.DENY), task.actions)
    }

    @Test
    fun parsesTaskErrorsWithoutInventingAvailableActions() {
        val task = taskItemFromFields(
            id = "task-2",
            title = "Build",
            status = "failed",
            detail = null,
            error = "Compiler exited with status 1",
            canApprove = false,
            canDeny = false,
            canCancel = false,
            canRetry = true,
        )

        assertEquals("Compiler exited with status 1", task.error)
        assertTrue(TaskAction.RETRY in task.actions)
        assertEquals(1, task.actions.size)
    }
}
