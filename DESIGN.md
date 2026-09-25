---
name: Voice AI
description: A calm native macOS menu-bar dictation and voice-control utility.
typography:
  title:
    fontFamily: "SF Pro, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.25
  body:
    fontFamily: "SF Pro, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.35
  label:
    fontFamily: "SF Pro, -apple-system, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.25
rounded:
  control: "6px"
  popover: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
---

# Design System: Voice AI

## Overview

**Creative North Star: "The Quiet Instrument"**

Voice AI follows macOS rather than imposing a separate visual world. It uses system appearance, materials, type, controls, and semantic state colors. Information is compact but never cramped; everyday controls remain distinct from diagnostics.

**The Native-First Rule.** If AppKit or SwiftUI already provides the expected macOS behavior, use it without ornamental replacement.

## Colors

Use semantic system colors only: label, secondary label, window background, separator, control accent, system red, orange, and green. This keeps contrast correct in both appearances and follows user accent preferences.

**The Restrained Accent Rule.** Accent color marks current action or state only; it is never decoration.

## Typography

Use SF Pro through system font APIs. Titles use semibold 13px, controls and body use regular 13px, and secondary status uses regular 11px. Never use display typography in product controls.

## Elevation

Use native popover and window elevation. Do not add custom shadows or glass effects; macOS supplies the correct depth and vibrancy.

## Components

- **Menu-bar icon:** monochrome SF Symbol, with shape changes and accessibility text for state.
- **Popover:** transient native popover, approximately 300px wide, with status first and Settings/Quit last.
- **Settings:** standard grouped form controls in a scrollable native window.
- **Shortcut field:** focusable bordered control that visibly enters capture mode and shows the recorded combination in macOS glyphs or canonical text.
- **Errors:** icon, concise message, and one recovery action. Never color alone.

## Do's and Don'ts

### Do:

- **Do** follow macOS light and dark appearance automatically.
- **Do** keep Settings and Quit visible from the primary popover.
- **Do** make recording, transcribing, success, and failure unambiguous.
- **Do** respect Reduce Motion and keyboard focus.

### Don't:

- **Don't** use malformed custom menus or one-pixel hosted views.
- **Don't** expose backend dashboards or diagnostic clutter in the primary surface.
- **Don't** swallow recording, shortcut, permission, or worker failures.
- **Don't** imitate Superwhisper branding or add decorative motion.
