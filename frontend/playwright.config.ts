import {defineConfig} from '@playwright/test'
export default defineConfig({
 testDir:'./e2e',testMatch:'**/*.pw.ts',workers:1,
 use:{baseURL:'http://127.0.0.1:5173',browserName:'chromium'},
 webServer:[
 {command:'python -m uvicorn chessbuilder.main:app --host 127.0.0.1 --port 8000',url:'http://127.0.0.1:8000/api/health',reuseExistingServer:false},
 {command:'npm run dev',url:'http://127.0.0.1:5173',reuseExistingServer:false}
 ]
})
