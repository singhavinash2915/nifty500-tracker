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

/**
 * The price chart's four marks, named rather than borrowed from the generic
 * slots. They are not interchangeable series: two moving averages, a demand
 * band and a supply band, and mistaking one for another is the whole failure
 * mode of the chart.
 *
 * Chosen after the validator failed the previous assignment. The two moving
 * averages had been orange and yellow — ΔE 13.7 in light and 10.6 in dark,
 * below the 15 floor, so they were genuinely hard to tell apart for anyone.
 * Validated as a set in both modes: `validate_palette.js
 * "#2a78d6,#eb6834,#1baf7a,#e34948"`.
 */
export const LEVELS = {
  light: { sma50: '#2a78d6', sma200: '#eb6834', support: '#1baf7a', resistance: '#e34948' },
  dark:  { sma50: '#3987e5', sma200: '#d95926', support: '#199e70', resistance: '#e66767' },
} as const

export function useLevels(dark: boolean) {
  return dark ? LEVELS.dark : LEVELS.light
}

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
