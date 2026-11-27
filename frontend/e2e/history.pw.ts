import {test,expect} from '@playwright/test'
test('import, dashboard and review a local game',async({page})=>{
 await page.goto('/');
 await page.getByRole('button',{name:'Importar',exact:true}).click();
 await page.getByLabel('Nome do jogador (opcional)').fill('Alice');
 await page.getByLabel('PGN',{exact:true}).fill('[White "Alice"]\n[Black "Bob"]\n[Date "2024.01.01"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0');
 await page.getByRole('button',{name:'Importar',exact:true}).last().click();
 await expect(page.getByRole('status').filter({hasText:'1 novas'})).toBeVisible();
 await page.getByRole('button',{name:'Visão geral',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Seu xadrez'})).toBeVisible();
 await expect(page.getByText('Partidas do perfil',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Retrospectiva'}).click();
 await expect(page.getByRole('dialog')).toBeVisible();
 await page.keyboard.press('ArrowRight');
 await expect(page.getByRole('dialog').getByRole('heading',{name:'Precisão'})).toBeVisible();
 await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Partidas',exact:true}).click();
 await page.getByRole('button',{name:'Revisar'}).click();
 await expect(page.getByRole('heading',{name:'Alice × Bob'})).toBeVisible();
 await page.getByRole('button',{name:'1. e4',exact:true}).click();
 await expect(page.getByRole('heading',{name:'e4',exact:true})).toBeVisible();
 await expect(page.locator('.square')).toHaveCount(64);
 await page.getByRole('button',{name:'→',exact:true}).click();
 await expect(page.getByRole('heading',{name:'e5',exact:true})).toBeVisible();
});
