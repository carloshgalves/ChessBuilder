import {expect,test} from 'vitest'
import {squares,START} from './Board'
test('board orientation',()=>{expect(squares(START)).toHaveLength(64);expect(squares(START)[0]).toBe('r');expect(squares(START,true)[0]).toBe('R')})

import {sanitize,sets,themes} from './Board'
test('appearance allowlists',()=>{expect(sets.length).toBe(7);expect(themes.length).toBe(12);expect(sanitize({theme:'unknown',pieces:'../x',motion:'x'})).toEqual({theme:'classic',pieces:'cburnett',motion:'fast'});expect(sanitize({pieces:'outline'}).pieces).toBe('spatial')})
