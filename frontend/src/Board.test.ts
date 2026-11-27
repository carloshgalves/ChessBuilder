import {expect,test} from 'vitest'
import {squares,START} from './Board'
test('board orientation',()=>{expect(squares(START)).toHaveLength(64);expect(squares(START)[0]).toBe('r');expect(squares(START,true)[0]).toBe('R')})
