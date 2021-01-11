# USB 固件相关开发

STM32提供的 USBD 库包括的核心代码部分已经由库提供, 内容为通讯协议的逻辑控制, 几乎不需要用户修改, 主要包括:

* usbd_ctlreq.c  该文件主要实现标准的usb 设备请求, 其中 USBD_StdDevReq(*pdev, *setup_req) 负责根据请求 USBD_SetupReqTypedef 中的请求类型 bmRequest 和 子类型bRequest 来执行后续操作:

  | REQ_TYPE 请求类型                           | 子类型                    | 响应 说明                                | 是否类设备定义 |
  | ------------------------------------------- | ------------------------- | ---------------------------------------- | -------------- |
  | USB_REQ_TYPE_CLASS<br />USB_REQ_TYPE_VENDOR |                           | 调用Setup 配置                           | 不同           |
  | USB_REQ_TYPE_STANDARD                       | USB_REQ_GET_DESCRIPTOR    | USB初始化时提供各种设备信息,以及配置信息 |                |
  |                                             | USB_REQ_SET_ADDRESS       |                                          |                |
  |                                             | USB_REQ_SET_CONFIGURATION |                                          |                |
  |                                             | USB_REQ_GET_CONFIGURATION |                                          |                |
  |                                             | USB_REQ_GET_STATUS        |                                          |                |
  |                                             | USB_REQ_SET_FEATURE       |                                          |                |
  |                                             | USB_REQ_CLEAR_FEATURE     |                                          |                |

  

* usbd_ioreq.c 主要负责数据传输, 这里实现了 通过ctl pipe对数据的发送, 继续发送; 准备接受, 继续接收; 和发送状态/接受状态

* usbd_core.c

* usbd_conf.c 

其他用户自己定义的代码包括:

* usb_device.c 初始化和USB的启动部分实现
* usbd_cdc_if.c 具体的 USB 设备类的实现
* usbd_desc.c 设备相关的自定义的描述符

## CDC (Class Definitions for Communications)
CDC 类型的USB 设备主要用来作为双向通讯

初始化的步骤如下:

1. 注册通用设备使用的 类型结构, 给定 Init, DeInit, Setup 等callback函数. 参照以下 USBD_ClassTypeDef 结构定义
2. 注册 CDC 专用的callback interface, 这里主要需要5个CDC 设备专用的callback
   * CDC_Init
   * CDC_DeInit
   * CDC_Control , 主要用来修改通信模式, 通信功能, 和通信速度等
   * CDC_Receive
   * CDC_TransmitCplt, 用来通知往PC方向的发送已经完成

### CDC_Control 通信速率控制

为了保证虚拟串口能在PC端正常打开, 这里 CDC_Control  需要正确响应 CDC_GET_LINE_CODING 的控制包请求, 这个在默认的CubeMX生成的 CDC 代码里是需要手动添加的: 

```c
/* USER CODE BEGIN PRIVATE_VARIABLES */
USBD_CDC_LineCodingTypeDef LineCoding =
  {
    115200, /* baud rate*/
    0x00,   /* stop bits-1*/
    0x00,   /* parity - none*/
    0x08    /* nb. of bits 8*/
  };
/* USER CODE END PRIVATE_VARIABLES */

static int8_t CDC_Control_FS(uint8_t cmd, uint8_t* pbuf, uint16_t length)
{
  /* USER CODE BEGIN 5 */
  switch(cmd)
  {
  ....
  /*******************************************************************************/
  /* Line Coding Structure                                                       */
  /*-----------------------------------------------------------------------------*/
  /* Offset | Field       | Size | Value  | Description                          */
  /* 0      | dwDTERate   |   4  | Number |Data terminal rate, in bits per second*/
  /* 4      | bCharFormat |   1  | Number | Stop bits                            */
  /*                                        0 - 1 Stop bit                       */
  /*                                        1 - 1.5 Stop bits                    */
  /*                                        2 - 2 Stop bits                      */
  /* 5      | bParityType |  1   | Number | Parity                               */
  /*                                        0 - None                             */
  /*                                        1 - Odd                              */
  /*                                        2 - Even                             */
  /*                                        3 - Mark                             */
  /*                                        4 - Space                            */
  /* 6      | bDataBits  |   1   | Number Data bits (5, 6, 7, 8 or 16).          */
  /*******************************************************************************/
    case CDC_SET_LINE_CODING:
        LineCoding.bitrate    = (uint32_t)(pbuf[0] | (pbuf[1] << 8) |\
                                (pbuf[2] << 16) | (pbuf[3] << 24));
        LineCoding.format     = pbuf[4];
        LineCoding.paritytype = pbuf[5];
        LineCoding.datatype   = pbuf[6];
    break;

    case CDC_GET_LINE_CODING:
        pbuf[0] = (uint8_t)(LineCoding.bitrate);
        pbuf[1] = (uint8_t)(LineCoding.bitrate >> 8);
        pbuf[2] = (uint8_t)(LineCoding.bitrate >> 16);
        pbuf[3] = (uint8_t)(LineCoding.bitrate >> 24);
        pbuf[4] = LineCoding.format;
        pbuf[5] = LineCoding.paritytype;
        pbuf[6] = LineCoding.datatype;
    break;
```

### IN --> to PC

往电脑host端发送的方向叫 IN , 通过设置buffer 和长度后, 调用 **USBD_CDC_TransmitPacket**

```c
USBD_CDC_SetTxBuffer(&hUsbDeviceFS, Buf, Len);
result = USBD_CDC_TransmitPacket(&hUsbDeviceFS);
```

完成后会调用 CDC_TransmitCplt

### OUT <-- from  PC

OTG_FS_IRQHandler 由系统被周期性的触发, 在收到PC端发送回来的数据后, 中断处理函数会调用 callback interface 注册中的 CDC_Receive 函数入口, 该函数默认调用 USBD_CDC_ReceivePacket 重复继续接收, 并最终在 USB_EP0StartXfer 函数中设置硬件控制传输的寄存器来接收新的数据, 因此这个 CDC_Receive 不应该阻断或运行过长时间.  这里可以自己添加其他的数据处理, 但更好的方式是异步模式下读取 USB RX buffer 

```c
static int8_t CDC_Receive_FS(uint8_t* Buf, uint32_t *Len)
{
  /* USER CODE BEGIN 6 */
  USBD_CDC_SetRxBuffer(&hUsbDeviceFS, &Buf[0]);
  USBD_CDC_ReceivePacket(&hUsbDeviceFS);
  return (USBD_OK);
  /* USER CODE END 6 */
}
```

### CDC 代码编译在 MDK-ARM 下的BUG

因为 MDK-ARM 编译下在malloc失败了, 因此需要在 usbd_conf.h 文件中换一个USBD_malloc 和 USBD_free的实现方式, 用 STM32CUBEIDE 的 arm-none-eabi-gcc 没有该问题. 或者在startup_stm32l476xx.s 里增加heap_size 到 0x400 就可以解决. 

![image-20210111140111143](.\image-20210111140111143.png)

```c
/* Memory management macros */

/** Alias for memory allocation. */
#define USBD_malloc         malloc

/** Alias for memory release. */
#define USBD_free          free

改为
/* Memory management macros */
#define USBD_malloc               (void *)USBD_static_malloc
#define USBD_free                 USBD_static_free    
```

其中 USBD_static_malloc 用静态分配

```C
/**
  * @brief  Static single allocation.
  * @param  size: Size of allocated memory
  * @retval None
  */
void *USBD_static_malloc(uint32_t size)
{
  static uint32_t mem[(sizeof(USBD_CDC_HandleTypeDef)/4)+1];/* On 32-bit boundary */
  return mem;
}

/**
  * @brief  Dummy memory free
  * @param  p: Pointer to allocated  memory address
  * @retval None
  */
void USBD_static_free(void *p)
{
// 留空
}
```



USB类型通用的callback 结构包括以下函数入口:

1. Init 初始化 使用参数有(*pdev, u8 cfg_index)
2. DeInit 终止化 使用参数有(*pdev, u8 cfg_index)
3. Setup 配置 (Endpoints控制相关) 使用参数有(*pdev, *setup_req结构指针)
4. EP0_TxSent 发送到Host完毕 (Endpoints控制相关) 使用参数有(*pdev)
5. EP0_RxReady 从Host接收到 (Endpoints控制相关) 使用参数有(*pdev)

另外不同类型设备的有

6. DataIn 发送给Host, 使用参数有 (*pdev, u8 ep_num)
7. DataOut 发送给 Device, 使用参数有 (*pdev, u8 ep_num)
8. SOF 帧开始 , 使用参数有 (*pdev)
9. IsoINIncomplete, 发送Host未完成
10. IsoOUTIncomplete, Host接收未完成

最后有如下获取 Config Descriptor 的

11. GetHSConfigDescriptor
12. GetFSConfigDescriptor
13. GetOtherSpeedConfigDescriptor
14. GetDeviceQualifierDescriptor

```c
typedef struct _Device_cb
{
  uint8_t (*Init)(struct _USBD_HandleTypeDef *pdev, uint8_t cfgidx);
  uint8_t (*DeInit)(struct _USBD_HandleTypeDef *pdev, uint8_t cfgidx);
  /* Control Endpoints*/
  uint8_t (*Setup)(struct _USBD_HandleTypeDef *pdev, USBD_SetupReqTypedef  *req);
  uint8_t (*EP0_TxSent)(struct _USBD_HandleTypeDef *pdev);
  uint8_t (*EP0_RxReady)(struct _USBD_HandleTypeDef *pdev);
  /* Class Specific Endpoints*/
  uint8_t (*DataIn)(struct _USBD_HandleTypeDef *pdev, uint8_t epnum);
  uint8_t (*DataOut)(struct _USBD_HandleTypeDef *pdev, uint8_t epnum);
  uint8_t (*SOF)(struct _USBD_HandleTypeDef *pdev);
  uint8_t (*IsoINIncomplete)(struct _USBD_HandleTypeDef *pdev, uint8_t epnum);
  uint8_t (*IsoOUTIncomplete)(struct _USBD_HandleTypeDef *pdev, uint8_t epnum);

  uint8_t  *(*GetHSConfigDescriptor)(uint16_t *length);
  uint8_t  *(*GetFSConfigDescriptor)(uint16_t *length);
  uint8_t  *(*GetOtherSpeedConfigDescriptor)(uint16_t *length);
  uint8_t  *(*GetDeviceQualifierDescriptor)(uint16_t *length);
#if (USBD_SUPPORT_USER_STRING_DESC == 1U)
  uint8_t  *(*GetUsrStrDescriptor)(struct _USBD_HandleTypeDef *pdev, uint8_t index,  uint16_t *length);
#endif

} USBD_ClassTypeDef;
```

## STM32L476 时钟配置

![image-20210111030757077](.\image-20210111030757077.png)