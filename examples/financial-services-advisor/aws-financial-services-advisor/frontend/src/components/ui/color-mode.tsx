import { IconButton } from '@chakra-ui/react'
import { LuMoon, LuSun } from 'react-icons/lu'
import { useColorMode } from '../../hooks/useColorMode'

/** Light/dark toggle. Chakra v3 reads the `.dark` class set by `useColorMode`. */
export function ColorModeButton() {
  const { colorMode, toggleColorMode } = useColorMode()
  const next = colorMode === 'dark' ? 'light' : 'dark'

  return (
    <IconButton
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      variant="ghost"
      size="sm"
      onClick={toggleColorMode}
    >
      {colorMode === 'dark' ? <LuSun /> : <LuMoon />}
    </IconButton>
  )
}

export default ColorModeButton
