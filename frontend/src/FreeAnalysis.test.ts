import {expect,test} from 'vitest'
import {squareCenter} from './Board'
import {addBranch,candidateScore} from './FreeAnalysis'
test('arrow coordinates respect orientation',()=>{expect(squareCenter('a1')).toEqual([.5,7.5]);expect(squareCenter('a1',true)).toEqual([7.5,.5]);expect(squareCenter('h8')).toEqual([7.5,.5])})
test('branches are deduplicated without losing alternatives',()=>{const b=addBranch([],['e2e4'],'e4');expect(addBranch(b,['e2e4'],'same')).toBe(b);expect(addBranch(b,['d2d4'],'d4')).toHaveLength(2)})
test('evaluation is white-relative',()=>{expect(candidateScore({cp:50,mate:null,depth:12,nodes:100,pv:[],line:[]},'black')).toBe('-0.50')})
