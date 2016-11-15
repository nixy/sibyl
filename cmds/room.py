#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Sibyl: A modular Python chat bot framework
# Copyright (c) 2015-2016 Joshua Haas <jahschwa.com>
# Copyright (c) 2016 Jonathan Frederickson <terracrypt.net>
#
# This file is part of Sibyl.
#
# Sibyl is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
################################################################################

import re,time,os,codecs

import requests

from sibyl.lib.decorators import *
from sibyl.lib.protocol import Message,Room
import sibyl.lib.util as util

MSG_CROSS = 'Cross protocol interaction is disabled'
MSG_MULTI = 'You must specify a room (I am in more than one)'
MSG_NONE = 'Protocol %s is not in any rooms'
MSG_MESS = 'You must specify a message'

@botconf
def conf(bot):
  """create config options"""

  return [
    {'name':'link_echo',
     'default':False,
     'parse':bot.conf.parse_bool,
     'valid':valid
    },
    {'name':'cross_proto',
     'default':True,
     'parse':bot.conf.parse_bool
    },
    {'name':'trigger_file',
     'default':'data/triggers.txt',
     'valid':bot.conf.valid_wfile
    }
  ]

def valid(conf,echo):
  """check for lxml"""
  
  if (not echo) or util.has_module('lxml'):
    return True
  
  conf.log('error',"Can't find module lxml; link_echo will be disabled")
  return False

@botinit
def init(bot):
  """create the pending_room variable to enable chat responses"""

  bot.add_var('pending_room',{})
  bot.add_var('pending_tell',[],persist=True)

  bot.add_var('triggers',{})
  try:
    bot.triggers = trigger_read(bot)
  except Exception as e:
    bot.log.error('Failed to parse trigger_file')
    bot.log.debug(e.message)

  if not util.has_module('lxml'):
    bot.log.debug("Can't find module lxml; unregistering link_echo hook")
    del bot.hooks['group']['room.link_echo']

@botcmd
def all(bot,mess,args):
  """highlight every user - all [proto room] message"""

  (pname,proto,room,args) = parse_args(bot,mess,args)
  error = check_args(bot,mess,pname,room,args)
  if error:
    return error

  proto.broadcast(' '.join(args),room,mess.get_from())

@botcmd
def say(bot,mess,args):
  """if in a room, say this in it - say [proto room] message"""

  (pname,proto,room,args) = parse_args(bot,mess,args)
  error = check_args(bot,mess,pname,room,args)
  if error:
    return error

  text = ' '.join(args)
  proto.send(text,room)

@botcmd(ctrl=True)
def join(bot,mess,args):
  """join a room - [proto room nick pass]"""

  (pname,proto,room,args) = parse_args(bot,mess,args)

  if not bot.opt('room.cross_proto') and pname!=mess.get_protocol().get_name():
    return MSG_CROSS

  if not args:
    return bot.run_cmd('rejoin',mess=mess)

  (name,nick,pword) = (args[0],bot.opt('nick_name'),None)

  # check for optional parameters
  if len(args)>1:
    nick = args[1]
  if len(args)>2:
    pword = args[2]

  room = proto.new_room(name,nick,pword)

  # add the room to pending_room so we can respond with a message later
  bot.pending_room[room] = mess
  proto.join_room(room)

@botcmd
def rejoin(bot,mess,args):
  """attempt to rejoin all rooms from the config file"""

  pnames = [mess.get_protocol().get_name()]
  if bot.opt('room.cross_proto'):
    pnames = bot.protocols.keys()

  # rejoin every room from the config file if we're not in them
  rejoined = []
  for pname in pnames:
    proto = bot.get_protocol(pname)
    for room in bot.opt('rooms').get(pname,[]):
      room = proto.new_room(room['room'],room['nick'],room['pass'])
      if not proto.in_room(room):
        rejoined.append(str(room))
        bot.pending_room[room] = mess
        proto.join_room(room)

  if rejoined:
    return 'Attempting to join rooms: '+str(rejoined)
  return 'No rooms to rejoin'

@botcmd(ctrl=True)
def leave(bot,mess,args):
  """leave the specified room - leave [proto room]"""

  (pname,proto,room,args) = parse_args(bot,mess,args)

  # if a room was specified check if it's valid, else use the invoker's room
  if not room:
    rooms = proto.get_rooms(Room.FLAG_ACTIVE)
    if args:
      if args[0] in [r.get_name() for r in rooms]:
        room = proto.new_room(args[0])
    elif len(rooms)==1:
      room = rooms[0]

  if not room:
    if rooms:
      return MSG_MULTI
    else:
      return MSG_NONE % pname

  # error on cross_proto and no room
  error = check_args(bot,mess,pname,room,['DUMMY_ARG'])
  if error:
    return error

  # leave the room or stop trying to reconnect
  in_room = proto.in_room(room)
  proto.part_room(room)
  if in_room:
    return 'Left room "%s"' % room
  return 'Reconnecting to room "%s" disabled' % room

@botcmd
def real(bot,mess,args):
  """return the real name of the given nick if known"""

  proto = mess.get_protocol()

  if not args:
    return 'You must specify a nick'

  room = mess.get_room()
  if not room:
    return 'This cmd only works in a room'
  nick = args[0]

  # respond with the user's real username if valid and known
  users = [user.get_name() for user in proto.get_occupants(room)]
  if nick not in users:
    return "I haven't seen nick \"%s\"" % nick
  return proto.get_real(room,nick).get_base()

@botcmd
def tell(bot,mess,args):
  """give a user a msg when they rejoin - tell [list|remove|nick msg]"""

  proto = mess.get_protocol()

  # default is to list existing tells
  if not args:
    args = ['list']

  # if invoked from a room, only list tells from that room
  if args[0]=='list':
    if mess.get_type()==Message.GROUP:
      rooms = [mess.get_room()]
    else:
      rooms = proto.get_rooms(Room.FLAG_ALL)
    tells = [x for x in bot.pending_tell if x[0] in rooms]
    if tells:
      return str([(t[0].get_name(),t[1],t[2]) for t in tells])
    return 'No saved tells'

  elif args[0]=='remove':
    if not mess.get_type()==Message.GROUP:
      return 'You can only remove tells in a room'
    l = len(bot.pending_tell)

    # if no nick specified, remove all tells for the invoker's room
    if len(args)==1:
      room = mess.get_room()
      bot.pending_tell = [x for x in bot.pending_tell if x[0]!=room]

    # if "*" specified remove all tells for that protocol
    elif args[1]=='*':
      bot.pending_tell = [x for x in bot.pending_tell
          if x[0].get_protocol()!=mess.get_protocol()]

    # if a nick is specified only remove tells for that nick
    else:
      bot.pending_tell = [x for x in bot.pending_tell
          if x[0]!=mess.get_room() or x[1]!=args[1]]
    return 'Removed %s tells' % (l-len(bot.pending_tell))

  if mess.get_type()!=Message.GROUP:
    return 'You can only use this cmd in a room'

  if len(args)<2:
    return 'You must specify a nick and a message'

  # compose a meaningful response for the specified user
  user = mess.get_user()
  room = frm.get_room()
  to = args[0]
  frm = user.get_name()
  msg = ' '.join(args[1:])
  t = time.asctime()

  # add it to pending_tell to act on when status changes
  msg = '%s: %s said "%s" at %s' % (to,frm,msg,t)
  bot.pending_tell.append((room,to,msg))
  return 'Added tell for "%s"' % to

@botcmd
def trigger(bot,mess,args):
  """manage triggers - (info|list|add|remove)"""

  if not args:
    args = ['info']

  if args[0]=='list':
    triggers = bot.triggers.keys()
    if not triggers:
      return 'There are no triggers'
    return ', '.join(sorted(triggers))

  elif args[0]=='add':
    if len(args)<2:
      return 'You must specify a name and message'
    if len(args)<3:
      return 'You must specify a message'
    (name,text) = (args[1],' '.join(args[2:]))

    if name in bot.triggers:
      return 'A trigger already exists by that name'
    if name in [h._sibylbot_dec_chat_name for h in bot.hooks['chat'].values()]:
      return 'The %s plugin has a chat command by that name' % bot.ns_cmd[name]

    bot.triggers[name] = text
    trigger_write(bot)
    return 'Added trigger "%s"' % name

  elif args[0]=='remove':
    if len(args)<2:
      return 'You must specify a trigger to remove'
    if args[1]=='*':
      bot.triggers = {}
      trigger_write(bot)
      return 'Removed all triggers'
    if args[1] not in bot.triggers:
      return 'Invalid trigger'
    del bot.triggers[args[1]]
    return 'Removed trigger "%s"' % args[1]

  return 'There are %s triggers' % len(bot.triggers)

@botgroup
def trigger_cb(bot,mess,cmd):
  """execute triggers"""

  if cmd and cmd[0] in bot.triggers:
    bot.send(bot.triggers[cmd[0]],mess.get_from())

def trigger_read(bot):
  """read triggers from file into dict"""

  fname = bot.opt('room.trigger_file')

  if os.path.isfile(fname):
    with codecs.open(bot.opt('room.trigger_file'),'r',encoding='utf8') as f:
      lines = f.readlines()
  else:
    lines = []
    trigger_write(bot)

  triggers = {}
  for (i,line) in enumerate(lines):
    try:
      if line.strip():
        (name,text) = line.split('\t')
        triggers[name.lower().strip()] = text.strip()
    except Exception as e:
      raise IOError('Error parsing trigger_file at line %s' % i)

  removed = False
  for name in triggers.keys():
    if name in [h._sibylbot_dec_chat_name for h in bot.hooks['chat'].values()]:
      removed = True
      del triggers[name]
      bot.log.warning('Ignoring trigger "%s"; conflicts with cmd from plugin %s'
          % (name,bot.ns_cmd[name]))

  if removed:
    bot.errors.append('Some triggers ignored due to name conflicts')

  return triggers

def trigger_write(bot):
  """write triggers from bot to file"""

  t = [(k,v) for (k,v) in sorted(bot.triggers.items(),key=lambda i:i[0])]
  lines = [(name+'\t'+text+'\n') for (name,text) in t]

  with codecs.open(bot.opt('room.trigger_file'),'w',encoding='utf8') as f:
    f.writelines(lines)

@botstatus
def tell_cb(bot,mess):
  """check if we have a pending "tell" for the user"""

  user = mess.get_user()
  room = mess.get_room()

  # we only care about status messages from rooms
  if not room:
    return

  # we only care about status messages for users entering the room
  (status,msg) = mess.get_status()
  if status<=Message.OFFLINE:
    return

  # check for tells matching the room and nick from the status and act on them
  name = user.get_name()
  new = []
  for x in bot.pending_tell:
    if x[0]!=room or x[1]!=name:
      new.append(x)
    else:
      bot.send(x[2],room)
  bot.pending_tell = new

@botgroup
def link_echo(bot,mess,cmd):
  """get the title of the linked webpage"""

  if not bot.opt('room.link_echo'):
    return

  try:
    from lxml.html import fromstring
  except ImportError:
    bot.log.error("Can't import lxml; disabling link_echo")
    bot.conf.opts['room.link_echo'] = False

  if cmd is not None:
    return

  msg = mess.get_text()
  try:
    titles = []
    urls = re.findall(r'(https?://[^\s]+)', msg)
    if len(urls)==0:
      return
    for url in urls:
      r = requests.get(url)
      title = fromstring(r.content).findtext('.//title')
      titles.append(title.strip())
    linkcount = 1
    reply = ""
    for title in titles:
      if title is not None:
        reply += "[" + str(linkcount)+ "] " + title + " "
        linkcount += 1
  except Exception as e:
    bot.log.error('Link echo - '+e.__class__.__name__+' - '+url)
  else:
    bot.send(reply,mess.get_from())

@botrooms
def _muc_join_success(bot,room):
  """notify user of join success"""

  _send_muc_result(bot,room,'Success joining room %s' % room)

@botroomf
def _muc_join_failure(bot,room,error):
  """notify user of join failure"""

  _send_muc_result(bot,room,'Failed to join room %s (%s)' % (room,error))

def _send_muc_result(bot,room,msg):
  """helper method for notifying user of result"""

  if room not in bot.pending_room:
    return
  mess = bot.pending_room[room]
  del bot.pending_room[room]
  
  bot.send(msg,mess.get_from())

def parse_args(bot,mess,args):
  """parse protocols and rooms out of args"""

  pname = mess.get_protocol().get_name()
  room = mess.get_room()

  if args and args[0] in bot.protocols:
    pname = args[0]
    del args[0]

  proto = bot.get_protocol(pname)

  if proto.get_rooms():

    if args:
      r = proto.new_room(args[0])
      if proto.in_room(r):
        room = r
        del args[0]

    # if they didn't specify a room, but we're only in one, use that
    if not room:
      rooms = proto.get_rooms()
      if len(rooms)==1:
        room = rooms[0]

  return (pname,proto,room,args)

def check_args(bot,mess,pname,room,args):
  """error checking for !all and !say"""

  if not bot.opt('room.cross_proto') and pname!=mess.get_protocol().get_name():
    return MSG_CROSS
  if not room:
    if bot.get_protocol(pname).get_rooms():
      return MSG_MULTI
    else:
      return MSG_NONE % pname
  if not args:
    return MSG_MESS
