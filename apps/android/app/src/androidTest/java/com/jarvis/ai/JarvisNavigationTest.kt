package com.jarvis.ai

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class JarvisNavigationTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun homeActionAndBottomNavigationOpenRealScreens() {
        composeRule.onNodeWithText("Start a chat").performClick()
        composeRule.onNodeWithText("Message").assertExists()

        composeRule.onNodeWithContentDescription("Tasks").performClick()
        composeRule.onNodeWithText("No tasks yet.").assertExists()

        composeRule.onNodeWithContentDescription("Memory").performClick()
        composeRule.onNodeWithText("No saved memories yet.").assertExists()
    }

    @Test
    fun settingsFormCanScrollToSignInControls() {
        composeRule.onNodeWithContentDescription("Settings").performClick()
        composeRule.onNodeWithText("Backend URL").assertExists()
        composeRule.onNodeWithText("Email").performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText("Password").performScrollTo().assertIsDisplayed()
    }
}
