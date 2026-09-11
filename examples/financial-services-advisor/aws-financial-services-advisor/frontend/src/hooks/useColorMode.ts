import { useCallback, useState } from 'react'

export type ColorMode = 'light' | 'dark'

const STORAGE_KEY = 'fsa-color-mode'

function readStoredMode(): ColorMode | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : null
  } catch {
    return null
  }
}

function systemMode(): ColorMode {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * Apply a colour mode to the document.
 *
 * Chakra v3 switches on a `.dark` class on the root element (see
 * `defaultConfig`'s `dark` condition), so toggling that class is all that is
 * needed - no `next-themes` dependency in a plain Vite app.
 */
function applyMode(mode: ColorMode) {
  const root = document.documentElement
  root.classList.toggle('dark', mode === 'dark')
  root.classList.toggle('light', mode === 'light')
  root.style.colorScheme = mode
  try {
    window.localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    // Private browsing: the preference simply does not persist.
  }
}

// Applied once at module load so the first paint is already in the right mode.
const initialMode = readStoredMode() ?? systemMode()
applyMode(initialMode)

export function useColorMode() {
  const [colorMode, setMode] = useState<ColorMode>(initialMode)

  const setColorMode = useCallback((mode: ColorMode) => {
    applyMode(mode)
    setMode(mode)
  }, [])

  const toggleColorMode = useCallback(() => {
    setMode((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      applyMode(next)
      return next
    })
  }, [])

  return { colorMode, setColorMode, toggleColorMode }
}
