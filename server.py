#!/bin/python
#Praise the libev sample

import time
import multiprocessing
from multiprocessing.reduction import reduce_handle, rebuild_handle
import Queue
import os
import sys
import re
import socket
import signal
import errno
import logging
import sys
import pyev
import ConfigParser

try:
    import resource
    resource.setrlimit(resource.RLIMIT_NOFILE, (5000000, -1))
except ValueError:
    pass 

logging.basicConfig(level=logging.INFO)

STOPSIGNALS = (signal.SIGINT, signal.SIGTERM)
NONBLOCKING = (errno.EAGAIN, errno.EWOULDBLOCK)
    
cfile = open('osiris.conf', 'r')   
config = ConfigParser.ConfigParser()
config.readfp(cfile)
cfile.close

try:
    debug = int(config.get("OSIRIS","debug"))
except:
    debug = 0

try:
    proxy = int(config.get("OSIRIS","proxy"))
except:
    proxy = 0

sys.path.append('app')
exec_app = {}
host_mod = {}
for i in range(len(config.sections())):
    if config.sections()[i] != "OSIRIS":
        _domain = config.sections()[i]
        _mod_name = config.get(config.sections()[i],'mod')
        exec_app[_domain] = __import__(_mod_name)
        host_mod[_domain] = _mod_name

class app():
    srv_str = "Server: OSIRIS Mach/4\r\n"

    def code(self,int):
        list = { 200: "200 OK", 500: "500 Server error", "ERR": "Unknown server error"}
        try:
            return list[int]
        except:
            return "{0} {1}".format(int,list["ERR"])

    def html(self,http_code,buf,head_str=''):
        payload = "HTTP/1.1 {0}\r\n".format(self.code(http_code))
        if (head_str):
            payload += head_str
        payload += "Content-Length: {0}\r\n\r\n".format(str(len(buf)))
        payload += buf
        return payload

    def header2dict(self,dict):
        hdict = { "TYPE": dict.split('\r\n',1)[0].split(' ',1)[0], "PATH": dict.split('\r\n',1)[0].split(' ',2)[1] }
        real_dict = dict.splitlines()

        for i in range(1, len(real_dict)):
            hdict[real_dict[i].split(':',1)[0]] = real_dict[i].split(':',1)[1].strip(' ')
        return hdict

    def hostname(self,buf):
        header_ln = re.findall("Host.*$",buf,re.MULTILINE)
        for ln in header_ln:
            return ln.split(' ',1)[1].split(':',1)[0]

    def ip(self,buf):
        header_ln = re.findall("X-Real-IP.*$",buf,re.MULTILINE)
        for ln in header_ln:
            return ln.split(' ',1)[1].split(':',1)[0]

    def gen_head(self,dict):
        head_str = self.srv_str
        try:
            for header, data in dict.iteritems():
                head_str += "{0}: {1}\r\n".format(header,data)
            return head_str
        except: 
            return head_str

    def respond(self,buf,addy):
        gen_head = self.gen_head
        hostname = self.hostname(buf)
        header2dict = self.header2dict

        if (proxy):
            addr_real = self.ip(buf)
            if (addr_real == None):
                addr_real = addy[0]
                logging.error("Reverse proxy not found!")
        else:
            addr_real = addy[0]

        if hostname not in exec_app:
            hostname = "fallback"
            logging.error("Hostname not found, fell to fallback")

            if hostname not in exec_app:
                logging.error("No fallback found")
                return self.html(500,"No fallback configured :(\r\n",head_str='')

        if (debug):
            reload(exec_app[hostname])

        try:
            try:
                buf_head = buf.split('\r\n\r\n',1)[0]
                buf_body = buf.split('\r\n\r\n',1)[1]
            except:
                buf_body = buf

            payload = { "header": header2dict(buf_head), "body": buf_body, "ip": addr_real }
            data = exec_app[hostname].reply(payload)

            try:
                file_path = "app/{0}/{1}".format(host_mod[hostname],data["file"])
                msg_file = open(file_path, 'r')
                msg = msg_file.read()
                msg_file.close()
            except:
                msg = data["msg"]

            try:
                temp_opt = data["template"]
                for entry, temp_opt_list in temp_opt.iteritems(): #super fast, man
                    msg = re.sub("({{.*?" + entry + ".*?}})", temp_opt[entry], msg)
                    msg = re.sub("({.*?" + entry + ".*?})", temp_opt[entry], msg)
            except:
                pass

            try:
                code = data["code"]
            except:
                code = 200
            try:
                head_str = gen_head(data['header'])
            except:
                head_str = self.srv_str

        except:
            code = 500
            msg = "Error parsing data\r\n"
            head_str = self.srv_str

        return self.html(code,msg,head_str)

class Connection(object):

    def __init__(self, sock, remote_address, loop, cnxn_id, parent):
        self.sock = sock
        self.remote_address = remote_address
        self.loop = loop
        self.cnxn_id = cnxn_id
        self.parent = parent

        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(0)
        self.sock.settimeout(0.5)

        self.buf = ""

        self.watcher = pyev.Io(self.sock.fileno(), pyev.EV_READ, self.loop, self.io_cb)
        self.watcher.start()

        logging.debug("[{0}:{1}] Connection ready with [{2}]".format(self.parent.name,self.cnxn_id,self.remote_address))

    def reset(self, events):
        self.watcher.stop()
        self.watcher.set(self.sock, events)
        self.watcher.start()

    def handle_error(self, msg, level=logging.ERROR, exc_info=True):
        if (debug):
            logging.info("[{0}:{1}] Error on connection with [{2}]:[{3}]".format(self.parent.name,self.cnxn_id,msg,self.remote_address),
                        exc_info=exc_info)
        self.close()

    def handle_read(self):
        buf = ""
        try:
            buf = self.sock.recv(2048)
        except Exception as err:
            if err.args[0] not in NONBLOCKING:
                self.handle_error("error reading from {0}".format(self.sock))
                return
            #else:
            #    return
        if len(buf):
            #logging.debug("[%s:%d] Received data: %s"%(self.parent.name,self.cnxn_id,buf))
            self.buf += buf

            self.resp = app().respond(buf,self.remote_address)

            self.reset(pyev.EV_READ | pyev.EV_WRITE)

        else:
            self.handle_error("Graceful connection closed by peer", logging.DEBUG, False)

    def handle_write(self):
        try:
            if self.buf:
                #sent = self.sock.send(self.buf)
                sent = self.sock.send(self.resp)
        except socket.error as err:
            if err.args[0] not in NONBLOCKING:
                self.handle_error("error writing to {0}".format(self.sock))
        else :
            self.reset(pyev.EV_READ)

    def io_cb(self, watcher, revents):
        if revents & pyev.EV_READ:
            self.handle_read()
        elif revents & pyev.EV_WRITE:
            self.handle_write()
        else:
            logging.debug("[%s:%d] io_cb called with unknown event %s"%(self.parent.name,self.cnxn_id,str(revents)))

    def close(self):
        self.sock.close()
        self.watcher.stop()
        self.watcher = None
        if (debug):
            logging.info("[{0}:{1}] Connection closed with [{2}]".format(self.parent.name,self.cnxn_id,self.remote_address))

class ServerWorker(multiprocessing.Process):
    def __init__(self,name,in_q,out_q):
        multiprocessing.Process.__init__(self,group=None,name=name)
        self.in_q = in_q
        self.out_q = out_q
        self.loop = pyev.Loop(flags=pyev.recommended_backends())
        self.watchers = []
        self.client_count = 0
        self.in_q_fd = self.in_q._reader.fileno()

        self.watchers.append(pyev.Io(self.in_q_fd, 
                                     pyev.EV_READ, 
                                     self.loop,
                                     self.in_q_cb))

        self.cnxns = {}

        logging.debug("ServerWorker[{0}:{1}]: Instantiated.".format(os.getpid(),self.name))

    def run(self):
        for watcher in self.watchers:
            watcher.start()

        if (debug):
            logging.info("ServerWorker[{0}:{1}]: Running...".format(os.getpid(),self.name))
        self.loop.start()

        if (debug):
            logging.info("ServerWorker[{0}:{1}]: Exited event loop!".format(os.getpid(),self.name))

    def stop(self):
        while self.watchers:
            self.watchers.pop().stop()

        self.loop.stop(pyev.EVBREAK_ALL)

        self.out_q.put("quitdone")

        if (debug):
            logging.info("ServerWorker[{0}:{1}]: Stopped!".format(os.getpid(),self.name))

        sys.exit(0)

    def reset(self, events):
        self.watchers[0].stop()
        self.watchers[0].set(self.in_q_fd, events)
        self.watchers[0].start()

    def signal_cb(self, watcher, revents):
        self.stop()

    def in_q_cb(self, watcher, revents):
        try:
            val = self.in_q.get()
            #val = self.in_q.get(True,interval)
            logging.debug("ServerWorker[{0}:{1}]: Received inQ event!".format(os.getpid(),self.name))
            if type(val) == type((1,)):

                # Construct a proper socket object from the socket FD
                client_socket_handle,client_address = val
                client_fd = rebuild_handle(client_socket_handle)
                client_socket = socket.fromfd(client_fd, socket.AF_INET, socket.SOCK_STREAM)

                logging.debug("ServerWorker[{0}:{1}]: Adding connection [{2}] from [{3}].".format(os.getpid(),self.name,self.client_count,client_address))

                self.client_count += 1
                self.cnxns[client_address] = Connection(client_socket, client_address, self.loop, self.client_count, self)

                self.reset(pyev.EV_READ)

            elif type(val) == type("") and val == "quit":
                if (debug):
                    logging.info("ServerWorker[{0}:{1}]: Received quit message!".format(os.getpid(),self.name))
                self.stop()

        except Queue.Empty:
            # Timed-out, carry on
            pass
        


class ServerMaster(object):
    def __init__(self, 
            start_server_ip="127.0.0.1",
            start_server_port=5000,
            num_server_workers=1):

        self.start_server_ip = start_server_ip
        self.start_server_port = start_server_port
        self.num_server_workers = num_server_workers
        self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen_sock.bind((start_server_ip,start_server_port))
        self.listen_sock.setblocking(0)
        self.listen_sock.settimeout(1)
        self.address = self.listen_sock.getsockname()

        self.worker_procs = []
        self.worker_queues = []

        for i in range(num_server_workers):

            # Create a pair of (inQ,outQ) for IPC with the worker
            worker_in_q = multiprocessing.Queue()
            worker_out_q = multiprocessing.Queue()

            self.worker_queues.append((worker_in_q,worker_out_q))

            # Create the worker process object
            worker_proc = ServerWorker("SW."+str(i+1), 
                                       worker_in_q,
                                       worker_out_q,
                                       )

            worker_proc.daemon = True
            self.worker_procs.append(worker_proc)
        
            # Start the worker process
            worker_proc.start()
    
        # By now the server workers have been spawned

        # Setup the default Pyev loop in the master 
        self.loop = pyev.default_loop(flags=pyev.recommended_backends())

        # Prepare signal , out Q and connection watchers
        self.sig_watchers = [pyev.Signal(sig, self.loop, self.signal_cb)
                              for sig in STOPSIGNALS]

        self.q_watchers = [pyev.Io(fd=worker.out_q._reader.fileno(), 
                                  events=pyev.EV_READ,
                                  loop=self.loop, 
                                  callback=self.out_q_cb,
                                  data=worker)
                            for worker in self.worker_procs]

        self.socket_watchers = [pyev.Io(fd=self.listen_sock.fileno(), 
                                        events=pyev.EV_READ, 
                                        loop=self.loop,
                                        callback=self.io_cb)]
        self.next_worker = 0

    def start(self):
        for watcher in self.sig_watchers:
            watcher.start()
        for watcher in self.q_watchers:
            watcher.start()
        for watcher in self.socket_watchers:
            watcher.start()

        self.listen_sock.listen(socket.SOMAXCONN)

        if (debug):
            logging.info("ServerMaster[{0}]: Started listening on [{1.address}]...".format(os.getpid(),self))
        print "OSIRIS is listening on {0}, port {1}".format(self.address[0],self.address[1])

        self.loop.start()

    def stop(self):
        if (debug):
            logging.info("ServerMaster[{0}]: Stop requested.".format(os.getpid()))

        for worker in self.worker_procs:
            worker.in_q.put("quit")

        while self.sig_watchers:
            self.sig_watchers.pop().stop()
        while self.q_watchers:
            self.q_watchers.pop().stop()
        while self.socket_watchers:
            self.socket_watchers.pop().stop()

        self.loop.stop(pyev.EVBREAK_ALL)

        self.listen_sock.close()

        for worker in self.worker_procs:
            worker.join()

        if (debug):
            logging.info("ServerMaster[{0}]: Stopped!".format(os.getpid()))
     
    def handle_error(self, msg, level=logging.ERROR, exc_info=True):
        logging.log(level, "ServerMaster[{0}]: Error: {1}".format(os.getpid(), msg),
                    exc_info=exc_info)
        self.stop()

    def signal_cb(self, watcher, revents):
        if (debug):
            logging.info("ServerMaster[{0}]: Signal triggered.".format(os.getpid()))
        self.stop()

    def io_cb(self, watcher, revents):
        try:

            while True: # Accept as much as possible
                try:
                    client_sock, client_address = self.listen_sock.accept()
                except socket.timeout as err:
                    break
                except socket.error as err:
                    if err.args[0] in NONBLOCKING: 
                        break
                    else:
                        raise
                else:
                    logging.debug("ServerMaster[{0}]: Accepted connection from [{1}].".format(os.getpid(),client_address))

                    # Forward the new client socket to a worker in a simple round robin fashion
                    self.worker_procs[self.next_worker].in_q.put((reduce_handle(client_sock.fileno()),client_address))
                    client_sock.close() # Close the socket on the master side
                    client_sock = None
        
                    self.next_worker += 1
                    if self.next_worker >= self.num_server_workers:
                        self.next_worker = 0

        except Exception:
            self.handle_error("Error accepting connection")

    def out_q_cb(self, watcher, revents):
        try:
            val = watcher.data.out_q.get()
            logging.debug("ServerMaster received outQ event from [%s] data [%s]"\
                    %(watcher.data.name,str(val))) 
            if type(val) == type((1,)):
                pass
            elif type(val) == type("") and val == "quitdone":
                logging.debug("ServerWorker [%s] has quit"\
                    %(watcher.data.name,)) 

        except Queue.Empty:
            #rip
            pass


if __name__ == "__main__":
    if (debug):
        logging.info("Debugging mode enabled")
    try:
        ip=config.get("OSIRIS","host")
        port=int(config.get("OSIRIS","port"))
        workers=int(config.get("OSIRIS","workers"))
    except:
        ServerMaster().handle_error("Error reading OSIRIS section of config")

    server_master = ServerMaster(
                            start_server_ip=ip,
                            start_server_port=port,
                            num_server_workers=workers)
    server_master.start()