# chum-bot

Chum Bot is IVPs' bestie!

- `chum`, `chummed`: Log chums with people by mentioning them in a message with the keyword `chum` or `chummed` and a picture of your chum. 
- `chumboard`: Show how many times each channel member has chummed with someone else. 
- `pics`: View all chums of a person by tagging them in a message with the `pics` keyword. 
- `referendum`: Reply `referendum` to a spot to start a 24-hour vote to determine if the chum will count or not. 
- `reset`: Reset the chum record in a channel. 


### Tips
- Look out for :white_check_mark:. If you don't see :white_check_mark:, your chum didn't count!
- If you didn't chum something correctly, you can edit your message within one minute of sending it to correct the error. 
- If you delete a message, the chum will no longer count! 
- Chumbot will treat each channel of the workspace separately so different teams can maintain their own chum boards. 

## Install
[Click here to install](https://spot-bot.onrender.com/spotbot/install/). (I know this looks really janky, but this is actually more secure than the native slack flow which does not protect against CSRF attacks.)

By installing Chum Bot, you acknowledge that you have read and agree to something. 

## Technical details
Developed and tested on Python 3.8.10 in Ubuntu. 
