import { useEffect, useState } from 'react'

/**
 * Chart palette.
 *
 * Slots 1-4 of the validated categorical set, in fixed order — assigned by
 * entity, never cycled and never reassigned when a filter changes the series
 * count. Both modes are chosen for their own surface rather than flipped.
 *
 * The validator reports slots 3 and 4 below 3:1 against the light surface, so
 * every chart using them ships visible labels or a legend. That is the relief
 * rule, not an optional nicety.
 */
export const SERIES = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500'],
} as const

/** Reserved for state, never reused as "series 5". */
export const STATUS = {
  good: { light: '#1baf7a', dark: '#199e70' },
  critical: { light: '#e34948', dark: '#e66767' },
} as const

export function useSeries(dark: boolean): readonly string[] {
  return dark ? SERIES.dark : SERIES.light
}

function readDark(): boolean {
  if (typeof window === 'undefined') return false
  const stamped = document.documentElement.getAttribute('data-theme')
  if (stamped === 'dark') return true
  if (stamped === 'light') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

/**
 * Theme as reactive state.
 *
 * Reading the media query once during render is not enough: the CSS follows
 * the theme immediately while the SVG keeps whatever palette it was built
 * with, so the chart ends up painting light-mode ink on a dark surface — the
 * price line and its legend swatch both disappeared. Charts colour themselves
 * in JavaScript, so they have to subscribe like anything else stateful.
 */
export function useDarkMode(): boolean {
  const [dark, setDark] = useState(readDark)

  useEffect(() => {
    const update = () => setDark(readDark())
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    media?.addEventListener('change', update)

    // The viewer's explicit choice arrives as an attribute on <html>, which
    // fires no media event of its own.
    const observer = new MutationObserver(update)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })

    update()
    return () => {
      media?.removeEventListener('change', update)
      observer.disconnect()
    }
  }, [])

  return dark
}
