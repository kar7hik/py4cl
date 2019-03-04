import sys
import numbers
import itertools

# For multi-dimensional arrays
import numpy

try:
    from io import StringIO # Python 3
except:
    from StringIO import StringIO

# Direct stdout to a StringIO buffer,
# to prevent commands from printing to the output stream

write_stream = sys.stdout
redirect_stream = StringIO()

sys.stdout = redirect_stream

class Symbol:
    """
    A wrapper around a string, representing a Lisp symbol. 
    """
    def __init__(self, name):
        self._name = name
    def __str__(self):
        return self._name
    def __repr__(self):
        return "Symbol("+self._name+")"

##################################################################
# This code adapted from cl4py
#
# https://github.com/marcoheisig/cl4py
#
# Copyright (c) 2018  Marco Heisig <marco.heisig@fau.de>

def lispify(obj):
    return lispify_aux(obj)

def lispify_aux(obj):
    try:
        return lispifiers[type(obj)](obj)
    except KeyError:
        # Special handling for numbers. This should catch NumPy types
        # as well as built-in numeric types
        if isinstance(obj, numbers.Number):
            return str(obj)
        
        # Another unknown type
        return "NIL"

def lispify_ndarray(obj):
    """Convert a NumPy array to a string which can be read by lisp
    Example:
       array([[1, 2],     => '#2A((1 2) (3 4))'
              [3, 4]])
    """
    def nested(obj):
        """Turns an array into nested ((1 2) (3 4))"""
        if obj.ndim == 1: 
            return "("+" ".join([lispify(i) for i in obj])+")" 
        return "(" + " ".join([nested(obj[i,...]) for i in range(obj.shape[0])]) + ")"

    return "#{:d}A".format(obj.ndim) + nested(obj)
    
lispifiers = {
    bool       : lambda x: "T" if x else "NIL",
    type(None) : lambda x: "NIL",
    int        : str,
    float      : str,
    complex    : lambda x: "#C(" + lispify_aux(x.real) + " " + lispify_aux(x.imag) + ")",
    list       : lambda x: "#(" + " ".join(lispify_aux(elt) for elt in x) + ")",
    tuple      : lambda x: "(" + " ".join(lispify_aux(elt) for elt in x) + ")",
    # Note: With dict -> hash table, use :test 'equal so that string keys work as expected
    dict       : lambda x: "#.(let ((table (make-hash-table :test 'equal))) " + " ".join("(setf (gethash {} table) {})".format(lispify(key), lispify(value)) for key, value in x.items()) + " table)",
    str        : lambda x: "\"" + x.replace("\\", "\\\\").replace('"', '\\"')  + "\"",
    Symbol     : str,
    numpy.ndarray : lispify_ndarray
}

##################################################################

eval_globals = {}
eval_locals = {}

def recv_string():
    """
    Get a string from the input stream
    """
    # First a line containing the length as a string
    length = int(sys.stdin.readline())
    # Then the specified number of bytes
    return sys.stdin.read(length)

def recv_value():
    """
    Get a value from the input stream
    Return could be any type
    """
    return eval(recv_string(), eval_globals, eval_locals)

def send_value(value):
    """
    Send a value to stdout as a string, with length of string first
    """
    value_str = lispify(value)
    print(len(value_str))
    write_stream.write(value_str)
    write_stream.flush()

def return_error(err):
    """
    Send an error message
    """
    try:
        sys.stdout = write_stream
        write_stream.write("e")
        send_value(str(err))
    finally:
        sys.stdout = redirect_stream

def return_value(value):
    """
    Send a value to stdout
    """
    if isinstance(value, Exception):
        return return_error(value)
    
    # Mark response as a returned value
    try:
        sys.stdout = write_stream
        write_stream.write("r")
        send_value(value)
    finally:
        sys.stdout = redirect_stream
        
def message_dispatch_loop():
    """
    Wait for a message, dispatch on the type of message.
    Message types are determined by the first character:

    e  Evaluate an expression (expects string)
    x  Execute a statement (expects string)
    q  Quit
    r  Return value from lisp (expects value)
    f  Function call
    a  Asynchronous function call
    R  Retrieve value from asynchronous call
    s  Set variable(s) 
    """
    while True:
        try:
            # Read command type
            cmd_type = sys.stdin.read(1)
            
            if cmd_type == "e":  # Evaluate an expression
                result = eval(recv_string(), eval_globals, eval_locals)
                return_value(result)
        
            elif cmd_type == "x": # Execute a statement
                exec(recv_string(), eval_globals, eval_locals)
                return_value(None)
            
            elif cmd_type == "q": # Quit
                sys.exit(0)
                
            elif cmd_type == "r": # Return value from Lisp function
                return recv_value()

            elif cmd_type == "f" or cmd_type == "a": # Function call
                # Get a tuple (function, allargs)
                fn_name, allargs = recv_value()

                # Split positional arguments and keywords
                args = []
                kwargs = {}
                if allargs:
                    it = iter(allargs) # Use iterator so we can skip values
                    for arg in it:
                        if isinstance(arg, Symbol):
                            # A keyword. Take the next value
                            kwargs[ str(arg)[1:] ] = next(it)
                            continue
                        args.append(arg)
                
                # Get the function object. Using eval to handle cases like "math.sqrt" or lambda functions
                function = eval(fn_name, eval_globals, eval_locals)
                if cmd_type == "f":
                    # Run function then return value
                    return_value( function(*args, **kwargs) )
                else:
                    # Asynchronous

                    # Get a handle, and send back to caller.
                    # The handle can be used to fetch
                    # the result using an "R" message.
                    
                    handle = next(async_handle)
                    return_value(handle)

                    try:
                        # Run function, store result
                        async_results[handle] = function(*args, **kwargs)
                    except Exception as e:
                        # Catching error here so it can
                        # be stored as the return value
                        async_results[handle] = e
            elif cmd_type == "R":
                # Request value using handle
                handle = recv_value()
                return_value( async_results.pop(handle) )
                
            elif cmd_type == "s":
                # Set variables. Should have the form
                # ( ("var1" value1) ("var2" value2) ...)
                setlist = recv_value()
                for name, value in setlist:
                    eval_locals[name] = value
                # Need to send something back to acknowlege
                return_value(True)
            else:
                return_error("Unknown message type '{0}'".format(cmd_type))
            
        except Exception as e:
            return_error(e)

        
def callback_func(ident, *args, **kwargs):
    """
    Call back to Lisp

    ident  Uniquely identifies the function to call
    args   Arguments to be passed to the function
    """

    # Convert kwargs into a sequence of ":keyword value" pairs
    # appended to the positional arguments
    allargs = args
    for key, value in kwargs.items():
        allargs += (Symbol(":"+str(key)), value)
    
    try:
        sys.stdout = write_stream
        write_stream.write("c")
        send_value((ident, allargs))
    finally:
        sys.stdout = redirect_stream

    # Wait for a value to be returned.
    # Note that the lisp function may call python before returning
    return message_dispatch_loop()

# Make callback function accessible to evaluation
eval_globals["_py4cl_callback"] = callback_func
eval_globals["_py4cl_Symbol"] = Symbol
eval_globals["_py4cl_np"] = numpy

async_results = {}  # Store for function results. Might be Exception
async_handle = itertools.count(0) # Running counter

# Main loop
message_dispatch_loop()



