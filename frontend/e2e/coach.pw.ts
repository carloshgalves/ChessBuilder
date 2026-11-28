import {test,expect} from '@playwright/test'
test('coach session and board interaction',async({page,request})=>{
 const csv='PuzzleId,FEN,Moves,Rating,Themes\ncoach-e2e,rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1,e2e4 e7e5,1500,fork\n';
 const imported=await request.post('/api/training/puzzles/import',{data:{csv_text:csv}});
 expect(imported.ok()).toBeTruthy();
 await page.goto('/');
 await page.getByRole('button',{name:'Treinador',exact:true}).click();
 await page.getByRole('spinbutton',{name:'Minutos'}).fill('5');
 await page.getByRole('button',{name:'Montar plano'}).click();
 await page.getByRole('link',{name:'Abrir',exact:true}).click();
 await expect(page.getByRole('button',{name:/^e7 /})).toBeVisible();
 await page.getByRole('button',{name:/^e7 /}).click();
 await page.getByRole('button',{name:'e5',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Correto'})).toBeVisible();
 await page.getByRole('link',{name:'Voltar à sessão'}).click();
 await page.getByRole('button',{name:'Concluir sessão'}).click();
 await expect(page.getByRole('button',{name:'Concluído'})).toBeDisabled();
});
