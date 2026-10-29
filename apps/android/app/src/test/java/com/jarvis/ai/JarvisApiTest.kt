package com.jarvis.ai

import org.junit.Assert.assertEquals
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
}
