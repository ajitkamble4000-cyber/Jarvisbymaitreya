package com.jarvis.ai.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val DarkColors = darkColorScheme(
    primary = Color(0xFF7DD3FC),
    secondary = Color(0xFFC4B5FD),
    tertiary = Color(0xFF67E8F9),
    background = Color(0xFF050B16),
    surface = Color(0xFF101B2A),
    surfaceVariant = Color(0xFF1B2A3B),
    onPrimary = Color(0xFF04101B),
    onSecondary = Color(0xFF120E22),
    onBackground = Color(0xFFE5F6FF),
    onSurface = Color(0xFFE5F6FF)
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF0F766E),
    secondary = Color(0xFF6D28D9),
    tertiary = Color(0xFF2563EB),
    background = Color(0xFFF5F8FF),
    surface = Color(0xFFFFFFFF),
    surfaceVariant = Color(0xFFE7ECFF),
    onPrimary = Color(0xFFFFFFFF),
    onSecondary = Color(0xFFFFFFFF),
    onBackground = Color(0xFF0F172A),
    onSurface = Color(0xFF0F172A)
)

@Composable
fun JarvisTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit
) {
    val colors = if (darkTheme) DarkColors else LightColors

    MaterialTheme(
        colorScheme = colors,
        content = content
    )
}
