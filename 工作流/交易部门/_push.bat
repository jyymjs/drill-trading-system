@echo off
cd /d "C:\Users\32032\Desktop\deepseek"
git add -A
git commit -m "chore: 周会视频全部处理完成(50/50) + 103份知识文档

- 50份周会/周课堂全部处理完成
- CLAUDE.md文档数更新 103
- 新增risk/ tracker/ 完整风控+追踪系统
- 资金管理: capital配置+价格校验+滚动统计+费率计算
- 蒙特卡洛模拟含交易成本

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
echo Done!
pause
