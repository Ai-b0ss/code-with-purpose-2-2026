// Optional Playwright integration. Uses one isolated demo server and synthetic data.
const { chromium } = require(process.env.GIM_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs'), os = require('os'), path = require('path'), net = require('net');
const { spawn } = require('child_process');
const assert = require('assert/strict');
(async () => {
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'gim-browser-'));
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
 const token='synthetic-browser-key-with-at-least-32-characters';
 const server=spawn(process.env.GIM_TEST_PYTHON || 'python',['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',String(port)],
  {cwd:path.resolve(__dirname,'..'),env:{...process.env,GIM_DB_PATH:path.join(tmp,'isolated.sqlite3'),GIM_EVALUATION_MODE:'demo',GIM_COORDINATOR_TOKEN:token,GIM_GOOGLE_SPREADSHEET_ID:''},stdio:'ignore'});
 let browser;
 try {
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/api/v1/health')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
  assert(ready,'Own isolated server must start');
  const custom=process.env.GIM_CHROMIUM_PATH;
  browser=await chromium.launch({headless:true,...(custom?{executablePath:custom,args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-zygote','--single-process','--use-gl=angle','--use-angle=swiftshader','--disable-features=Vulkan']}:{})});
  const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const receipts=[];page.on('response',async r=>{if(r.url().endsWith('/api/v1/applications') && r.request().method()==='POST' && r.status()===201)receipts.push(await r.json());});
  let failedClarification=false,failedSubmit=false,reloadedAfterFailure=false;const submissionPayloads=[];
  await page.route('**/api/v1/clarification',async route=>{
   if(!failedClarification){failedClarification=true;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'clarification_provider_timeout'})});}
   else await route.continue();
  });
  await page.route('**/api/v1/applications',async route=>{
   if(route.request().method()!=='POST')return route.continue();
   submissionPayloads.push(route.request().postDataJSON());
   if(!failedSubmit){failedSubmit=true;const saved=await route.fetch();assert.equal(saved.status(),201);assert.equal((await saved.json()).created,true);await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'evaluator_provider_timeout'})});}
   else await route.continue();
  });
  await page.goto(base);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#applicant_name').fill('Синтетический Кандидат Тестович');
  await page.locator('#email').fill('synthetic@example.test');await page.locator('#vk_url').fill('https://vk.com/synthetic');
  await page.locator('#birth_date').fill('2020-01-01');await page.locator('#age_over_18').check();await page.locator('#consent_personal_data').check();
  await page.locator('#nextBtn').click();assert.match(await page.locator('#errorBox').innerText(),/18/);
  await page.locator('#birth_date').fill('2000-01-01');await page.locator('#university').selectOption('Другое');await page.locator('#university_other').fill('Тестовый вуз');
  await page.locator('#faculty').fill('История');await page.locator('#course').selectOption('2-й курс бакалавриата');await page.locator('#scientific_interests').fill('История');
  await page.locator('input[name="foreign_languages"][value="Нет B1+"]').locator('..').click();
  await page.reload();assert.equal(await page.locator('#applicant_name').inputValue(),'Синтетический Кандидат Тестович');
  assert(await page.locator('#consent_personal_data').isChecked());await page.locator('#nextBtn').click();
  await page.locator('#motivation').fill('Хочу помогать гостям исторического музея и развивать музейные образовательные проекты.');
  await page.locator('#experience').fill('Нет опыта\nХочу учиться; 🙂');await page.locator('#expectations').fill('Обучение и интересные задачи');await page.locator('#hobbies').fill('Чтение');
  await page.locator('input[name="source"][value="Другое"]').locator('..').click();await page.locator('#nextBtn').click();
  await page.locator('input[name="desired_direction"][value="media"]').locator('..').click();await page.locator('#availability_hours_month').fill('16');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile form must fit');
  await page.locator('#nextBtn').click();await page.locator('#errorBox').filter({hasText:'clarification_provider_timeout'}).waitFor();
  assert(await page.locator('#nextBtn').isEnabled());assert.equal(await page.locator('#availability_hours_month').inputValue(),'16');
  await page.locator('#nextBtn').click();await page.locator('.step[data-step="3"]').waitFor({state:'visible'});
  await page.locator('#prevBtn').click();
  const readDraft=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('gim_volunteer_form_draft_v1')));
  await page.locator('.preset-btn[data-hours="16"]').click();assert.equal((await readDraft()).clarification_round,1);
  await page.locator('.preset-btn[data-hours="24"]').click();assert.equal((await readDraft()).clarification_round,0);
  assert.deepEqual((await readDraft()).followup_questions,[]);
  await page.locator('.day-chip').first().click();assert.equal((await readDraft()).availability_days.length,1);
  await page.locator('#nextBtn').click();await page.locator('.step[data-step="3"]').waitFor({state:'visible'});
  for(let attempt=0;attempt<5;attempt++){
   for(const q of await page.locator('#clarificationsList textarea').all())await q.fill('Пока не знаю');
   await page.locator('#submitBtn').click();
   try{await page.locator('#results').waitFor({state:'visible',timeout:2000});break;}catch{}
   if(failedSubmit && !reloadedAfterFailure){
    await page.locator('#errorBox').filter({hasText:'evaluator_provider_timeout'}).waitFor();
    assert(await page.locator('#submitBtn').isEnabled());assert(await page.locator('#prevBtn').isEnabled());
    assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('gim_volunteer_form_draft_v1')).application_id),submissionPayloads[0].application_id);
    await page.reload();assert.equal(await page.locator('#experience').inputValue(),'Нет опыта\nХочу учиться; 🙂');
    for(let step=0;step<3;step++)await page.locator('#nextBtn').click();
    await page.locator('.step[data-step="3"]').waitFor({state:'visible'});reloadedAfterFailure=true;
   }
  }
  await page.locator('#results').waitFor({state:'visible'});
  assert.match(await page.locator('#candidateFeedback').innerText(),/медиа/);
  assert(failedClarification && reloadedAfterFailure);assert.equal(submissionPayloads.length,2);assert.deepEqual(submissionPayloads[0],submissionPayloads[1]);
  assert.equal(receipts.length,1);assert.equal(receipts[0].created,false);assert(!('evaluation' in receipts[0]));
  assert.equal(await page.evaluate(()=>sessionStorage.length),0);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow on mobile results');
  const out=process.env.GIM_BROWSER_OUTPUT;
  if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'candidate-mobile.png'),fullPage:true});}
  await page.goto(base+'/admin');await page.locator('#token').fill('wrong');await page.locator('#login button').click();await page.locator('#error').filter({hasText:'401'}).waitFor();
  await page.locator('#token').fill(token);await page.locator('#login button').click();await page.locator('#rows button').first().waitFor();
  assert.match(await page.locator('#meta').innerText(),/Выборка: 1/);
  assert.equal(await page.evaluate(()=>localStorage.length),0);
  await page.locator('#rows button').first().click();await page.locator('#card').waitFor({state:'visible'});await page.locator('#detail summary').click();assert.match(await page.locator('#detail').innerText(),/Нет опыта/);await page.locator('#close').click();
  await page.setViewportSize({width:1280,height:900});if(out)await page.screenshot({path:path.join(out,'coordinator-desktop.png'),fullPage:true});
  const dl=page.waitForEvent('download');await page.locator('#csv').click();assert.match((await dl).suggestedFilename(),/csv/);
  await page.locator('#logout').click();assert(await page.locator('#workspace').isHidden());
  assert.deepEqual(errors,[]);console.log('BROWSER_ACCEPTANCE_PASS desktop+390px mobile, underage, complete questionnaire, clarification, lost receipt after commit+refresh keeps same payload/ID without duplicate, receipt, coordinator auth/card/csv/logout, no JS errors');
 } finally {if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
