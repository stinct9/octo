import { describe, expect, it } from 'vitest'
import { bandPath, dayTicks, linear, linePath, nearest, niceMax, radius, yTicks } from './chart'

describe('the fillers chart', () => {
  it('rounds the top of the axis to a clean value and ticks it', () => {
    expect(niceMax(4.4)).toBe(6)
    expect(niceMax(0.3)).toBe(2)
    expect(niceMax(13)).toBe(15)
    expect(yTicks(6)).toEqual([0, 2, 4, 6])
    expect(yTicks(15)).toEqual([0, 5, 10, 15])
  })
  it('scales and finds the nearest point', () => {
    const x = linear(0, 10, 100, 200)
    expect(x(5)).toBe(150)
    expect(nearest([10, 20, 40], 33)).toBe(2)
    expect(nearest([], 3)).toBe(-1)
  })
  it('labels local days, thinned to fit', () => {
    const a = new Date(2026, 9, 1, 10).getTime() / 1000
    const b = new Date(2026, 9, 20, 10).getTime() / 1000
    expect(dayTicks(a, b, 50)).toHaveLength(20)
    const few = dayTicks(a, b, 5)
    expect(few.length).toBeLessThanOrEqual(5)
    expect(new Date(few[0] * 1000).getHours()).toBe(0)
  })
  it('sizes dots by words and draws the line and band', () => {
    expect(radius(0)).toBe(4)
    expect(radius(5000)).toBe(9)
    expect(linePath([0, 10], [5, 6])).toBe('M0.0,5.0L10.0,6.0')
    expect(bandPath([0, 10], [8, 9], [2, 3])).toBe('M0.0,2.0L10.0,3.0L10.0,9.0L0.0,8.0Z')
  })
})
