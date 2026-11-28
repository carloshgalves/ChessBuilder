import {expect,test} from 'vitest'
import {clockText,elapsedText,moveRows} from './ReplayComponents'
test('paired rows preserve black-to-move starting position',()=>{const a={ply:1,san:'Kf7',color:'black' as const,move_number:17,clock:null,elapsed_seconds:null,elapsed_source:null};const b={...a,ply:2,san:'e4',color:'white' as const,move_number:18};expect(moveRows([a,b])).toEqual([{number:17,black:a},{number:18,white:b}]);expect(clockText(302)).toBe('5:02');expect(clockText(null)).toBe('—');expect(elapsedText(0)).toBe('0 s')})
