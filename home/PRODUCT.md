# Andromeda Home

## Platform
android

## Purpose
Dragoon's home screen is declared in Andromeda configuration and applied without
screen gestures. The owner requested four columns of apps and a four-app dock,
with no widgets. Managed patched apps take precedence over stock equivalents.

## Constraints
Use the installed Android SDK and standard Java APIs. Configuration is local and
restricted to the ADB shell. No network or sensitive runtime permissions. Keep
Pixel Launcher available for rollback. All other apps remain accessible.

## Accessibility
Use labeled app icons, native focus and press feedback, Android touch targets,
font scaling, system insets, and a scrollable grid on smaller displays.
